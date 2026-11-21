"""Connected Apps orchestration API."""
from __future__ import annotations
import secrets,time,threading
from core.connected_apps.models import AppConnection,AppDescriptor,ConnectionStatus,TransportType
from core.connected_apps.registry import registry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.runtime import runtime

def _serialize(row):
    return {"id":row.id,"app_id":row.app_id,"display_name":row.display_name,"status":row.status.value,
        "enabled":row.enabled,"auth_type":row.auth_type.value,"scopes":list(row.scopes),
        "created_at":row.created_at,"updated_at":row.updated_at,"last_error":row.last_error,
        "metadata":dict(row.metadata)}

class ConnectedAppsService:
    def __init__(self,app_registry=None,store=None):
        self.registry=app_registry or registry
        self.store=store or ConnectedAppsStore()\n        self._adapter_lock=threading.RLock()\n        self._adapters={}
    @staticmethod
    def normalize(name):
        return " ".join(str(name or "").strip().casefold().split())
    def register(self,adapter,replace=False):
        key=self.normalize(getattr(adapter,"name",""))
        if not key or not callable(getattr(adapter,"execute",None)):
            raise TypeError("Adapter must have a non-empty name and execute()")
        with self._adapter_lock:
            if key in self._adapters and not replace: raise ValueError(f"Adapter '{key}' already registered")
            self._adapters[key]=adapter
    def get(self,name):
        key=self.normalize(name)
        with self._adapter_lock:
            direct=self._adapters.get(key)
            if direct:return direct
            matches=[a for a in self._adapters.values() if self.normalize(getattr(a,"display_name",""))==key]
            return matches[0] if len(matches)==1 else None
    def unregister(self,name):
        key=self.normalize(name)
        with self._adapter_lock:
            if key in self._adapters:return self._adapters.pop(key,None) is not None
            matches=[k for k,a in self._adapters.items() if self.normalize(getattr(a,"display_name",""))==key]
            return self._adapters.pop(matches[0],None) is not None if len(matches)==1 else False
    def list_apps(self):
        with self._adapter_lock:
            return [{"name":a.name,"display_name":getattr(a,"display_name",a.name)} for a in self._adapters.values()]
    def summary(self):
        return ", ".join(a["display_name"] for a in self.list_apps())
    def catalog(self,query="",category=""):
        return self.registry.search(query,category=category)
    def connections(self):
        return self.store.list_connections()
    def connect_stdio(self,descriptor,display_name=None,connection_id=None):
        cid=connection_id or secrets.token_urlsafe(16)
        row=AppConnection(id=cid,app_id=descriptor.id,display_name=display_name or descriptor.name,
            status=ConnectionStatus.CONNECTING,auth_type=descriptor.auth_type,scopes=list(descriptor.scopes),
            created_at=time.time(),metadata={"transport":"stdio","command":descriptor.command,"args":list(descriptor.args)})
        self.store.upsert_connection(_serialize(row))
        try:
            tools=runtime.connect_stdio(cid,descriptor.command,descriptor.args,descriptor.env)
            row.status=ConnectionStatus.CONNECTED
            row.metadata["tools"]=tools
        except Exception as exc:
            row.status=ConnectionStatus.ERROR
            row.last_error=f"{type(exc).__name__}: {exc}"
        data=_serialize(row);self.store.upsert_connection(data);return data
    def connect_http(self,descriptor,display_name=None,connection_id=None,headers=None):
        cid=connection_id or secrets.token_urlsafe(16)
        headers=dict(headers or {})
        row=AppConnection(id=cid,app_id=descriptor.id,display_name=display_name or descriptor.name,
            status=ConnectionStatus.CONNECTING,auth_type=descriptor.auth_type,scopes=list(descriptor.scopes),
            created_at=time.time(),metadata={"transport":"http","endpoint":descriptor.endpoint})
        self.store.upsert_connection(_serialize(row))
        if headers.get("Authorization"):
            self.store.set_credential(cid,"authorization_header",headers["Authorization"])
        try:
            tools=runtime.connect_http(cid,descriptor.endpoint,headers=headers)
            row.status=ConnectionStatus.CONNECTED;row.metadata["tools"]=tools
        except Exception as exc:
            row.status=ConnectionStatus.ERROR;row.last_error=f"{type(exc).__name__}: {exc}"
        data=_serialize(row);self.store.upsert_connection(data);return data
    def reconnect(self,cid):
        row=self.store.get_connection(cid)
        if not row:return None
        meta=row.get("metadata",{})
        descriptor=AppDescriptor(id=row["app_id"],name=row["display_name"],source="stored",
            transport=TransportType.HTTP if meta.get("transport")=="http" else TransportType.STDIO,
            endpoint=meta.get("endpoint",""),command=meta.get("command",""),args=meta.get("args",[]))
        if descriptor.transport==TransportType.HTTP:
            return self.connect_http(descriptor,connection_id=cid,headers=self._saved_http_headers(cid))
        return self.connect_stdio(descriptor,connection_id=cid)
    def _saved_http_headers(self,cid):
        provider=self.store.get_credential(cid,"oauth_provider")
        if provider:
            from core.connected_apps.auth_service import OAuthService
            token=OAuthService(self.store).get_access_token(cid)
            token_type=self.store.get_credential(cid,"token_type") or "Bearer"
            return {"Authorization":f"{token_type} {token}"}
        value=self.store.get_credential(cid,"authorization_header")
        return {"Authorization":value} if value else {}
    def disconnect(self,cid):
        runtime.disconnect(cid)
        row=self.store.get_connection(cid)
        if not row:return False
        row["status"]=ConnectionStatus.DISCONNECTED.value
        row["updated_at"]=time.time()
        self.store.upsert_connection(row)
        return True
    def remove(self,cid):
        runtime.disconnect(cid)
        return self.store.remove_connection(cid)
    def tools(self,cid):
        return runtime.tools(cid)
    def call_tool(self,cid,tool,arguments=None):
        return runtime.call(cid,tool,arguments or {})

service=ConnectedAppsService()
