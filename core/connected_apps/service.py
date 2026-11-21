"""Connected Apps orchestration API."""
from __future__ import annotations
import secrets,time
from core.connected_apps.models import AppConnection,AppDescriptor,ConnectionStatus,TransportType
from core.connected_apps.registry import AppRegistry,registry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.runtime import runtime

def _serialize(row):
    return {"id":row.id,"app_id":row.app_id,"display_name":row.display_name,"status":row.status.value,
        "enabled":row.enabled,"auth_type":row.auth_type.value,"scopes":list(row.scopes),
        "created_at":row.created_at,"updated_at":row.updated_at,"last_error":row.last_error,
        "metadata":dict(row.metadata)}

class ConnectedAppsService:
    def __init__(self,app_registry=None,store=None):
        self.registry=app_registry or registry; self.store=store or ConnectedAppsStore()
    def catalog(self,query="",category=""): return self.registry.search(query,category=category)
    def connections(self): return self.store.list_connections()
    def connect_stdio(self,descriptor,display_name=None,connection_id=None):
        cid=connection_id or secrets.token_urlsafe(16)
        row=AppConnection(id=cid,app_id=descriptor.id,display_name=display_name or descriptor.name,
            status=ConnectionStatus.CONNECTING,auth_type=descriptor.auth_type,scopes=list(descriptor.scopes),
            created_at=time.time(),metadata={"transport":"stdio","command":descriptor.command,"args":list(descriptor.args)})
        self.store.upsert_connection(_serialize(row))
        try:
            tools=runtime.connect_stdio(cid,descriptor.command,descriptor.args,descriptor.env)
            row.status=ConnectionStatus.CONNECTED; row.metadata["tools"]=tools
        except Exception as exc: row.status=ConnectionStatus.ERROR; row.last_error=f"{type(exc).__name__}: {exc}"
        data=_serialize(row); self.store.upsert_connection(data); return data
    def connect_http(self,descriptor,display_name=None,connection_id=None,headers=None):
        cid=connection_id or secrets.token_urlsafe(16)
        row=AppConnection(id=cid,app_id=descriptor.id,display_name=display_name or descriptor.name,
            status=ConnectionStatus.CONNECTING,auth_type=descriptor.auth_type,scopes=list(descriptor.scopes),
            created_at=time.time(),metadata={"transport":"http","endpoint":descriptor.endpoint})
        self.store.upsert_connection(_serialize(row))
        try:
            tools=runtime.connect_http(cid,descriptor.endpoint,headers=headers)
            row.status=ConnectionStatus.CONNECTED; row.metadata["tools"]=tools
        except Exception as exc: row.status=ConnectionStatus.ERROR; row.last_error=f"{type(exc).__name__}: {exc}"
        data=_serialize(row); self.store.upsert_connection(data); return data
    def reconnect(self,cid):
        row=self.store.get_connection(cid)
        if not row: return None
        meta=row.get("metadata",{})
        descriptor=AppDescriptor(id=row["app_id"],name=row["display_name"],source="stored",
            transport=TransportType.HTTP if meta.get("transport")=="http" else TransportType.STDIO,
            endpoint=meta.get("endpoint",""),command=meta.get("command",""),args=meta.get("args",[]))
        return self.connect_http(descriptor,connection_id=cid) if descriptor.transport==TransportType.HTTP else self.connect_stdio(descriptor,connection_id=cid)
    def disconnect(self,cid):
        runtime.disconnect(cid); row=self.store.get_connection(cid)
        if not row:return False
        row["status"]=ConnectionStatus.DISCONNECTED.value; row["updated_at"]=time.time()
        self.store.upsert_connection(row); return True
    def remove(self,cid): runtime.disconnect(cid); return self.store.remove_connection(cid)
    def tools(self,cid): return runtime.tools(cid)
    def call_tool(self,cid,tool,arguments=None): return runtime.call(cid,tool,arguments or {})
service=ConnectedAppsService()
