"""Connected Apps settings UI for optional MCP integrations."""
from __future__ import annotations
import json
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget, QListWidgetItem, QMessageBox, QInputDialog, QGroupBox, QCheckBox, QApplication
from core.connected_apps.models import AppDescriptor, TransportType
from core.connected_apps.service import service

class _Signals(QObject):
    finished=pyqtSignal(object,object)
class _Worker(QRunnable):
    def __init__(self,fn):
        super().__init__();self.fn=fn;self.signals=_Signals()
    def run(self):
        try:result=self.fn();error=None
        except Exception as exc:result=None;error=exc
        self.signals.finished.emit(result,error)

class ConnectedAppsPanel(QWidget):
    connections_changed=pyqtSignal()
    def __init__(self,parent=None):
        super().__init__(parent);self.pool=QThreadPool.globalInstance();self.setObjectName("ConnectedAppsPanel")
        root=QVBoxLayout(self);root.setContentsMargins(4,4,4,4);root.setSpacing(14)
        title=QLabel("CONNECTED APPS");title.setObjectName("appsTitle")
        subtitle=QLabel("Connect external services and MCP servers. Integrations are optional and isolated from JARVIS core.")
        subtitle.setObjectName("appsSubtitle");subtitle.setWordWrap(True);root.addWidget(title);root.addWidget(subtitle)
        group=QGroupBox("Connection setup");layout=QVBoxLayout(group);layout.setSpacing(10)
        toolbar=QHBoxLayout();self.search=QLineEdit();self.search.setObjectName("appsSearch");self.search.setPlaceholderText("Search connected apps…");self.search.textChanged.connect(self.refresh);toolbar.addWidget(self.search,1)
        self.add_catalog_btn=QPushButton("Catalog");self.add_catalog_btn.clicked.connect(self.add_from_catalog);self.add_stdio_btn=QPushButton("+ Add STDIO");self.add_stdio_btn.clicked.connect(self.add_custom)
        self.add_http_btn=QPushButton("+ Add HTTP");self.add_http_btn.clicked.connect(self.add_http);self.add_sse_btn=QPushButton("+ Add SSE");self.add_sse_btn.clicked.connect(self.add_sse)
        self.add_oauth_btn=QPushButton("+ Provider OAuth");self.add_oauth_btn.clicked.connect(self.add_oauth_http);self.add_mcp_oauth_btn=QPushButton("+ MCP OAuth");self.add_mcp_oauth_btn.clicked.connect(self.add_mcp_oauth);self.reconnect_all_btn=QPushButton("Reconnect all");self.reconnect_all_btn.clicked.connect(self.reconnect_all)
        toolbar.addWidget(self.add_catalog_btn);toolbar.addWidget(self.add_stdio_btn);toolbar.addWidget(self.add_http_btn);toolbar.addWidget(self.add_sse_btn);toolbar.addWidget(self.add_oauth_btn);toolbar.addWidget(self.add_mcp_oauth_btn);toolbar.addWidget(self.reconnect_all_btn);layout.addLayout(toolbar)
        self.list=QListWidget();self.list.setObjectName("appsList");self.list.setMinimumHeight(100);self.list.setMaximumHeight(190);self.list.itemSelectionChanged.connect(self.show_selected);layout.addWidget(self.list)
        self.details=QTextEdit();self.details.setObjectName("appsDetails");self.details.setReadOnly(True);self.details.setMinimumHeight(100);self.details.setMaximumHeight(170);layout.addWidget(self.details)
        actions=QHBoxLayout();self.connect_btn=QPushButton("Connect / Reconnect");self.connect_btn.clicked.connect(self.connect_selected);self.disconnect_btn=QPushButton("Disconnect");self.disconnect_btn.clicked.connect(self.disconnect_selected);self.remove_btn=QPushButton("Remove");self.remove_btn.clicked.connect(self.remove_selected);self.block_tool_btn=QPushButton("Block tool");self.block_tool_btn.clicked.connect(lambda:self.set_tool_permission(True));self.allow_tool_btn=QPushButton("Unblock tool");self.allow_tool_btn.clicked.connect(lambda:self.set_tool_permission(False));self.copy_diagnostics_btn=QPushButton("Copy diagnostics");self.copy_diagnostics_btn.clicked.connect(self.copy_diagnostics);self.health_btn=QPushButton("Check health");self.health_btn.clicked.connect(self.check_selected_health)
        actions.addWidget(self.connect_btn);actions.addWidget(self.disconnect_btn);actions.addWidget(self.remove_btn);actions.addWidget(self.block_tool_btn);actions.addWidget(self.allow_tool_btn);actions.addWidget(self.copy_diagnostics_btn);actions.addWidget(self.health_btn);actions.addStretch(1)layout.addLayout(actions);self.auto_read_checkbox=QCheckBox("Auto-run read-only MCP tools from this server");self.auto_read_checkbox.setToolTip("Only tools marked read-only and non-destructive by the MCP server can run without confirmation. Enable only for servers you trust.");self.auto_read_checkbox.toggled.connect(self._set_auto_read);layout.addWidget(self.auto_read_checkbox);root.addWidget(group)
        runtime=QGroupBox("MCP runtime");rrow=QVBoxLayout(runtime);rrow.setSpacing(6);self.runtime_status=QLabel();self.runtime_status.setWordWrap(True);rrow.addWidget(self.runtime_status);self.runtime_help=QLabel();self.runtime_help.setWordWrap(True);rrow.addWidget(self.runtime_help);root.addWidget(runtime)
        self.status=QLabel("Ready");self.status.setObjectName("appsStatus");self.status.setWordWrap(True);root.addWidget(self.status);root.addStretch(1)
        self.setStyleSheet("""
        #appsTitle { font-size: 18px; font-weight: 700; letter-spacing: 1px; }
        #appsSubtitle { color: palette(mid); }
        QGroupBox { border: 1px solid palette(mid); border-radius: 10px; margin-top: 10px; padding: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        QLineEdit, QTextEdit, QListWidget { background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 7px; padding: 8px; }
        QListWidget::item { padding: 8px; border-radius: 5px; margin: 2px; }
        QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
        QPushButton { border: 1px solid palette(mid); border-radius: 7px; padding: 8px 12px; }
        QPushButton:hover { border-color: palette(highlight); }
        #appsStatus { padding: 5px; }
        """)
        self._check_runtime();self.refresh()
    def _check_runtime(self):
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.runtime_status.setText("MCP SDK is not installed in this environment.")
            self.runtime_help.setText("Developer setup: run  uv sync --extra connected-apps  from the project root. This keeps MCP optional and out of JARVIS core.")
        else:
            self.runtime_status.setText("MCP SDK is available. STDIO and HTTP transports are ready.")
            self.runtime_help.setText("Connected Apps runtime is available.")

    def _busy(self,busy,message=""):
        for w in (self.add_catalog_btn,self.add_stdio_btn,self.add_http_btn,self.add_sse_btn,self.add_oauth_btn,self.add_mcp_oauth_btn,self.reconnect_all_btn,self.connect_btn,self.disconnect_btn,self.remove_btn,self.block_tool_btn,self.allow_tool_btn,self.copy_diagnostics_btn,self.health_btn):w.setEnabled(not busy)
        if message:self.status.setText(message)
    def refresh(self,*_):
        query=self.search.text().casefold().strip();selected=self.selected_id();self.list.clear();visible=[]
        for row in service.connections():
            if query and query not in (row.get("display_name","")+" "+row.get("app_id","")).casefold():continue
            visible.append(row);item=QListWidgetItem(f"{row.get('display_name','Unnamed')}  ·  {row.get('status','unknown')}");item.setData(Qt.ItemDataRole.UserRole,row.get("id"));self.list.addItem(item)
            if selected and item.data(Qt.ItemDataRole.UserRole)==selected:self.list.setCurrentItem(item)
        if not visible:
            item=QListWidgetItem("No connected apps yet. Add an MCP server above." if not query else "No apps match your search.");item.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(item)
        self.show_selected()
    def selected_id(self):
        item=self.list.currentItem();return item.data(Qt.ItemDataRole.UserRole) if item else None
    def show_selected(self):
        cid=self.selected_id();row=service.store.get_connection(cid) if cid else None
        if not row:
            self.details.setPlainText(
                "Select a connection to inspect status and discovered tools.\n\n"
                "STDIO launches a local MCP process. HTTP connects to a remote endpoint. "
                "OAuth opens your browser for authorization."
            )
            self.auto_read_checkbox.setEnabled(False)
            self.auto_read_checkbox.blockSignals(True)
            self.auto_read_checkbox.setChecked(False)
            self.auto_read_checkbox.blockSignals(False)
            return
        meta=row.get("metadata",{});tools=meta.get("tools",[]) or service.tools(cid);self.auto_read_checkbox.setEnabled(row.get("status")=="connected");self.auto_read_checkbox.blockSignals(True);self.auto_read_checkbox.setChecked(meta.get("auto_approve_read_only") is True);self.auto_read_checkbox.blockSignals(False)
        lines=[f"App: {row.get('display_name')}",f"Status: {row.get('status')}",f"Transport: {meta.get('transport','unknown')}",f"Endpoint: {meta.get('endpoint') or meta.get('command','')}",f"Last error: {row.get('last_error') or 'None'}","",f"Tools ({len(tools)}):"]
        lines.extend(f"• {t.get('name')} [{'BLOCKED' if t.get('name') in set(meta.get('blocked_tools',[])) else 'AVAILABLE'}]: {t.get('description','')}\n  Schema: {json.dumps(t.get('input_schema',{}),ensure_ascii=False)}" for t in tools)
        if row.get("last_error"):lines.extend(["","Troubleshooting:",self._friendly_error(row["last_error"])])
        self.details.setPlainText("\n".join(lines))
    def _set_auto_read(self, enabled):
        cid=self.selected_id()
        if not cid:return
        row=service.store.get_connection(cid)
        if not row:return
        metadata=dict(row.get("metadata") or {})
        metadata["auto_approve_read_only"]=bool(enabled)
        row["metadata"]=metadata
        service.store.upsert_connection(row)
        self.status.setText("Read-only auto-run enabled. Use only with trusted MCP servers." if enabled else "Read-only auto-run disabled; all MCP tools require confirmation.")
        self.refresh()

    @staticmethod
    def _friendly_error(error):
        if "MCP SDK is missing" in error or "No module named 'mcp'" in error:return "Install the optional Connected Apps dependency with: uv sync --extra connected-apps"
        if "FileNotFoundError" in error:return "Command not found. Install the server runtime and verify executable PATH."
        if "timed out" in error.lower():return "Server did not respond. Check command, network and logs."
        return error
    @staticmethod
    def _slug(value):return "-".join(value.strip().lower().split()) or "app"
    def add_from_catalog(self):
        from core.connected_apps.catalog import list_templates
        templates=list_templates()
        labels=[f"{item.name} — {item.description}" for item in templates]
        label,ok=QInputDialog.getItem(self,"MCP catalog","Choose a server template:",labels,0,False)
        if not ok:return
        template=templates[labels.index(label)]
        args,ok=QInputDialog.getText(self,"MCP catalog",f"Arguments for {template.command} (JSON array):",text=json.dumps(list(template.args),ensure_ascii=False))
        if not ok:return
        env_text,ok=QInputDialog.getText(self,"MCP catalog","Environment variables (JSON object):",text="{}")
        if not ok:return
        try:
            parsed=json.loads(args)
            env=json.loads(env_text)
            if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):raise ValueError("Arguments must be a JSON array of strings.")
            if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):raise ValueError("Environment must be a JSON object of string values.")
            missing=[key for key in template.env_keys if key not in env]
            if missing:raise ValueError("Missing required environment variables: "+", ".join(missing))
        except Exception as exc:
            QMessageBox.warning(self,"Invalid configuration",str(exc))
            return
        descriptor=AppDescriptor(
            id="catalog-"+template.id,
            name=template.name,
            description=template.description,
            source="catalog",
            transport=TransportType.STDIO,
            command=template.command,
            args=parsed,
            env=env,
        )
        self._connect_descriptor(descriptor)

    def add_custom(self):
        name,ok=QInputDialog.getText(self,"Add STDIO MCP server","Display name:")
        if not ok or not name.strip():return
        command,ok=QInputDialog.getText(self,"STDIO MCP server","Executable (npx, uvx, python):")
        if not ok or not command.strip():return
        args,ok=QInputDialog.getText(self,"STDIO MCP server","Arguments (JSON string array):",text="[]")
        if not ok:return
        env_text,ok=QInputDialog.getText(self,"STDIO MCP server","Environment variables (JSON object):",text="{}")
        if not ok:return
        try:
            parsed=json.loads(args)
            env=json.loads(env_text)
            if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):raise ValueError("Arguments must be a JSON array of strings.")
            if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):raise ValueError("Environment must be a JSON object of string values.")
        except Exception as exc:QMessageBox.warning(self,"Invalid configuration",str(exc));return
        d=AppDescriptor(id="custom-"+self._slug(name),name=name.strip(),description="Custom MCP server",source="user",transport=TransportType.STDIO,command=command.strip(),args=parsed,env=env);self._connect_descriptor(d)
    def add_http(self):
        name,ok=QInputDialog.getText(self,"Add MCP HTTP server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP HTTP server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        token,ok=QInputDialog.getText(self,"Authentication","Bearer token (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        d=AppDescriptor(id="http-"+self._slug(name),name=name.strip(),description="Custom MCP HTTP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip());headers={"Authorization":f"Bearer {token}"} if token.strip() else {};self._connect_http_descriptor(d,headers=headers)
    def add_sse(self):
        name,ok=QInputDialog.getText(self,"Add MCP SSE server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP SSE server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        token,ok=QInputDialog.getText(self,"Authentication","Bearer token (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        d=AppDescriptor(id="sse-"+self._slug(name),name=name.strip(),description="Custom MCP SSE server",source="user",transport=TransportType.SSE,endpoint=endpoint.strip())
        headers={"Authorization":f"Bearer {token}"} if token.strip() else {}
        self._busy(True,f"Connecting to {d.name}…")
        w=_Worker(lambda:service.connect_sse(d,headers=headers))
        w.signals.finished.connect(self._connected)
        self.pool.start(w)
    def add_mcp_oauth(self):
        name,ok=QInputDialog.getText(self,"MCP OAuth","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP OAuth","Protected MCP resource URL:")
        if not ok or not endpoint.strip():return
        client_id,ok=QInputDialog.getText(self,"MCP OAuth","OAuth Client ID:")
        if not ok or not client_id.strip():return
        secret,ok=QInputDialog.getText(self,"MCP OAuth","Client Secret (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        scopes,ok=QInputDialog.getText(self,"MCP OAuth","Scopes (space-separated):")
        if not ok:return
        d=AppDescriptor(id="mcp-oauth-"+self._slug(name),name=name.strip(),description="MCP OAuth protected resource",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip())
        self._busy(True,"Discovering OAuth metadata and opening browser…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            auth=OAuthService(service.store).authorize_mcp_connection(d.id,endpoint.strip(),client_id.strip(),scopes=scopes.split(),client_secret=secret)
            token=OAuthService(service.store).get_access_token(d.id)
            typ=service.store.get_credential(d.id,"token_type") or "Bearer"
            return service.connect_http(d,connection_id=d.id,headers={"Authorization":f"{typ} {token}"})
        w=_Worker(authorize)
        w.signals.finished.connect(self._connected)
        self.pool.start(w)
    def add_oauth_http(self):
        from core.connected_apps.providers import list_providers
        providers=list_providers();names=[p.name for p in providers]
        if not names:QMessageBox.information(self,"OAuth","No OAuth providers configured.");return
        pname,ok=QInputDialog.getItem(self,"OAuth provider","Provider:",names,0,False)
        if not ok:return
        p=next((x for x in providers if x.name==pname),None)
        if not p:return
        name,ok=QInputDialog.getText(self,"OAuth MCP server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"OAuth MCP server","MCP HTTP URL:")
        if not ok or not endpoint.strip():return
        client_id,ok=QInputDialog.getText(self,"OAuth setup","OAuth Client ID:")
        if not ok or not client_id.strip():return
        secret,ok=QInputDialog.getText(self,"OAuth setup","Client Secret (if required):",QLineEdit.EchoMode.Password)
        if not ok:return
        scopes,ok=QInputDialog.getText(self,"OAuth setup","Scopes (space-separated):",text=" ".join(p.default_scopes))
        if not ok:return
        d=AppDescriptor(id="oauth-"+p.id+"-"+self._slug(name),name=name.strip(),description=f"{p.name} OAuth MCP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip());self._busy(True,"Opening browser for OAuth…")
        def authorize():
            from core.connected_apps.auth_service import OAuthService
            OAuthService(service.store).authorize_connection(d.id,p.id,client_id.strip(),scopes=scopes.split(),client_secret=secret)
            token=service.store.get_credential(d.id,"access_token");typ=service.store.get_credential(d.id,"token_type") or "Bearer"
            return service.connect_http(d,connection_id=d.id,headers={"Authorization":f"{typ} {token}"})
        w=_Worker(authorize);w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connect_http_descriptor(self,d,cid=None,headers=None):
        self._busy(True,f"Connecting to {d.name}…");w=_Worker(lambda:service.connect_http(d,connection_id=cid,headers=headers));w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connect_descriptor(self,d,cid=None):
        self._busy(True,f"Connecting to {d.name}…");w=_Worker(lambda:service.connect_stdio(d,connection_id=cid));w.signals.finished.connect(self._connected);self.pool.start(w)
    def reconnect_all(self):
        if not service.connections():
            self.status.setText("No saved connections to reconnect.")
            return
        self._busy(True,"Reconnecting saved MCP connections…")
        w=_Worker(service.reconnect_all);w.signals.finished.connect(self._reconnect_all_done);self.pool.start(w)
    def _reconnect_all_done(self,results,error):
        self._busy(False)
        if error:
            self.status.setText(f"Reconnect all failed: {self._friendly_error(str(error))}")
            QMessageBox.warning(self,"Reconnect all",self._friendly_error(str(error)))
            return
        connected=sum(1 for row in results if row.get("status")=="connected")
        failed=len(results)-connected
        self.status.setText(f"Reconnect complete: {connected} connected, {failed} failed.")
        if failed:
            details="\n".join(f"{row.get('name')}: {row.get('error') or row.get('status')}" for row in results if row.get("status")!="connected")
            QMessageBox.warning(self,"Reconnect results",details)
        self.refresh()
        self.connections_changed.emit()

    def check_selected_health(self):
        cid=self.selected_id()
        if not cid:
            QMessageBox.information(self,"Health check","Select a connection first.")
            return
        self._busy(True,"Checking MCP connection health…")
        worker=_Worker(lambda:service.health_check(cid))
        worker.signals.finished.connect(self._health_checked)
        self.pool.start(worker)

    def _health_checked(self,result,error):
        self._busy(False)
        if error:
            self.status.setText(f"Health check failed: {self._friendly_error(str(error))}")
            QMessageBox.warning(self,"Health check",self._friendly_error(str(error)))
        elif result:
            status="healthy" if result.get("healthy") else "unhealthy"
            self.status.setText(f"Connection health: {status}")
        self.refresh()

    def connect_selected(self):
        cid=self.selected_id()
        if cid:self._busy(True,"Reconnecting…");w=_Worker(lambda:service.reconnect(cid));w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connected(self,row,error):
        self._busy(False)
        if error:
            msg=self._friendly_error(str(error));self.status.setText(f"Connection failed: {msg}");QMessageBox.warning(self,"Connection failed",msg)
        elif row:
            count=len(row.get("metadata",{}).get("tools",[]));self.status.setText(f"{row.get('display_name')}: {row.get('status')} · {count} tools discovered")
            if row.get("status")=="connected":self.connections_changed.emit()
            if row.get("last_error"):QMessageBox.warning(self,"Connection failed",self._friendly_error(row["last_error"]))
        self.refresh()
    def copy_diagnostics(self):
        cid=self.selected_id()
        row=service.store.get_connection(cid) if cid else None
        if not row:
            QMessageBox.information(self,"Diagnostics","Select a connection first.")
            return
        metadata=dict(row.get("metadata") or {})
        safe_metadata={key:value for key,value in metadata.items() if key not in {"authorization","token","secret"}}
        diagnostics={
            "app_id":row.get("app_id"),
            "display_name":row.get("display_name"),
            "status":row.get("status"),
            "auth_type":row.get("auth_type"),
            "scopes":row.get("scopes"),
            "created_at":row.get("created_at"),
            "updated_at":row.get("updated_at"),
            "last_error":self._friendly_error(row.get("last_error") or ""),
            "metadata":safe_metadata,
            "runtime_tools":service.tools(cid),
        }
        QApplication.clipboard().setText(json.dumps(diagnostics,ensure_ascii=False,indent=2))
        self.status.setText("Connection diagnostics copied. Credentials are excluded.")

    def set_tool_permission(self, blocked):
        cid=self.selected_id()
        if not cid:
            QMessageBox.information(self,"Tool permissions","Select a connected app first.")
            return
        row=service.store.get_connection(cid)
        tools=service.tools(cid)
        names=[item.get("name","") for item in tools if item.get("name")]
        if not names:
            QMessageBox.information(self,"Tool permissions","Connect the server and discover its tools first.")
            return
        name,ok=QInputDialog.getItem(self,"Tool permissions","Tool:",names,0,False)
        if not ok or not name:
            return
        try:
            service.set_tool_blocked(cid,name,blocked)
            self.status.setText(f"{'Blocked' if blocked else 'Unblocked'} MCP tool: {name}")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self,"Tool permissions",self._friendly_error(str(exc)))

    def disconnect_selected(self):
        cid=self.selected_id()
        if not cid:return
        self._busy(True,"Disconnecting…");w=_Worker(lambda:service.disconnect(cid));w.signals.finished.connect(lambda r,e:self._operation_done("Disconnected",e));self.pool.start(w)
    def remove_selected(self):
        cid=self.selected_id()
        if not cid:return
        if QMessageBox.question(self,"Remove connection","Remove this connection and stored credentials?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self._busy(True,"Removing…");w=_Worker(lambda:service.remove(cid));w.signals.finished.connect(lambda r,e:self._operation_done("Removed",e));self.pool.start(w)
    def _operation_done(self,message,error):
        self._busy(False);self.status.setText(message if not error else f"Error: {error}")
        if error:QMessageBox.warning(self,"Connected Apps",self._friendly_error(str(error)))
        self.refresh()
        if not error:self.connections_changed.emit()
