"""Connected Apps settings UI for optional MCP integrations."""
from __future__ import annotations
import json, subprocess, sys
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit, QListWidget, QListWidgetItem, QMessageBox, QInputDialog, QGroupBox
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
    def __init__(self,parent=None):
        super().__init__(parent);self.pool=QThreadPool.globalInstance();self.setObjectName("ConnectedAppsPanel")
        root=QVBoxLayout(self);root.setContentsMargins(4,4,4,4);root.setSpacing(14)
        title=QLabel("CONNECTED APPS");title.setObjectName("appsTitle")
        subtitle=QLabel("Connect external services and MCP servers. Integrations are optional and isolated from JARVIS core.")
        subtitle.setObjectName("appsSubtitle");subtitle.setWordWrap(True);root.addWidget(title);root.addWidget(subtitle)
        group=QGroupBox("Connection setup");layout=QVBoxLayout(group);layout.setSpacing(10)
        toolbar=QHBoxLayout();self.search=QLineEdit();self.search.setObjectName("appsSearch");self.search.setPlaceholderText("Search connected apps…");self.search.textChanged.connect(self.refresh);toolbar.addWidget(self.search,1)
        self.add_stdio_btn=QPushButton("+ Add STDIO");self.add_stdio_btn.clicked.connect(self.add_custom)
        self.add_http_btn=QPushButton("+ Add HTTP");self.add_http_btn.clicked.connect(self.add_http)
        self.add_oauth_btn=QPushButton("+ Add OAuth");self.add_oauth_btn.clicked.connect(self.add_oauth_http)
        toolbar.addWidget(self.add_stdio_btn);toolbar.addWidget(self.add_http_btn);toolbar.addWidget(self.add_oauth_btn);layout.addLayout(toolbar)
        self.list=QListWidget();self.list.setObjectName("appsList");self.list.setMinimumHeight(150);self.list.itemSelectionChanged.connect(self.show_selected);layout.addWidget(self.list)
        self.details=QTextEdit();self.details.setObjectName("appsDetails");self.details.setReadOnly(True);self.details.setMinimumHeight(130);self.details.setMaximumHeight(220);layout.addWidget(self.details)
        actions=QHBoxLayout();self.connect_btn=QPushButton("Connect / Reconnect");self.connect_btn.clicked.connect(self.connect_selected);self.disconnect_btn=QPushButton("Disconnect");self.disconnect_btn.clicked.connect(self.disconnect_selected);self.remove_btn=QPushButton("Remove");self.remove_btn.clicked.connect(self.remove_selected)
        actions.addWidget(self.connect_btn);actions.addWidget(self.disconnect_btn);actions.addWidget(self.remove_btn);actions.addStretch(1);layout.addLayout(actions);root.addWidget(group)
        runtime=QGroupBox("MCP runtime");rrow=QHBoxLayout(runtime);self.runtime_status=QLabel();self.runtime_status.setWordWrap(True);rrow.addWidget(self.runtime_status,1);self.install_btn=QPushButton("Install MCP support");self.install_btn.clicked.connect(self.install_mcp);rrow.addWidget(self.install_btn);root.addWidget(runtime)
        self.status=QLabel("Ready");self.status.setObjectName("appsStatus");self.status.setWordWrap(True);root.addWidget(self.status);root.addStretch(1)
        self.setStyleSheet("""
        #ConnectedAppsPanel{color:#d9f7f4} #appsTitle{font-size:18px;font-weight:700;letter-spacing:1px;color:#52eee0}
        #appsSubtitle{color:#8ba9a8} QGroupBox{border:1px solid #155d60;border-radius:10px;margin-top:10px;padding:12px;font-weight:600;color:#6df4e8}
        QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px}
        QLineEdit,QTextEdit,QListWidget{background:#031315;border:1px solid #15565a;border-radius:7px;padding:8px;color:#d9f7f4;selection-background-color:#07504d}
        QListWidget::item{padding:9px;border-radius:5px;margin:2px} QListWidget::item:selected{background:#06403f;color:#66fff0}
        QPushButton{background:#052326;border:1px solid #176a6d;border-radius:7px;padding:8px 12px;color:#63eee3}
        QPushButton:hover{background:#0a393b;border-color:#42d8ce} QPushButton:disabled{color:#567171;border-color:#244344}
        #appsStatus{color:#80d8d1;padding:5px}
        """)
        self._check_runtime();self.refresh()
    def _check_runtime(self):
        try:import mcp
        except ImportError:
            self.runtime_status.setText("MCP SDK missing. Install it to enable STDIO and HTTP connections.")
            self.install_btn.setEnabled(True)
        else:
            self.runtime_status.setText("MCP SDK installed. STDIO and HTTP connections are available.")
            self.install_btn.setText("MCP support installed");self.install_btn.setEnabled(False)
    def install_mcp(self):
        self._busy(True,"Bootstrapping pip and installing MCP support…")
        def install():
            bootstrap=subprocess.run([sys.executable,"-m","ensurepip","--upgrade"],capture_output=True,text=True,timeout=180)
            if bootstrap.returncode != 0:
                raise RuntimeError("ensurepip failed:\n"+(bootstrap.stderr or bootstrap.stdout or "No diagnostic output"))
            return subprocess.run([sys.executable,"-m","pip","install","--upgrade","pip","mcp>=1.0,<2"],capture_output=True,text=True,timeout=600,check=True)
        w=_Worker(install);w.signals.finished.connect(self._installed);self.pool.start(w)
    def _installed(self,result,error):
        self._busy(False)
        if error:
            detail=getattr(error,"stderr","") or str(error)
            if not detail and getattr(error,"__cause__",None):detail=str(error.__cause__)
            QMessageBox.warning(self,"MCP installation failed",detail[-4000:]);self.status.setText("MCP installation failed. See diagnostic details.")
        else:
            self._check_runtime();self.status.setText("MCP SDK installed. Restart JARVIS if needed.")
    def _busy(self,busy,message=""):
        for w in (self.add_stdio_btn,self.add_http_btn,self.add_oauth_btn,self.connect_btn,self.disconnect_btn,self.remove_btn,self.install_btn):w.setEnabled(not busy)
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
        if not row:self.details.setPlainText("Select a connection to inspect status and discovered tools.\n\nSTDIO launches a local MCP process. HTTP connects to a remote endpoint. OAuth opens your browser for authorization.");return
        meta=row.get("metadata",{});tools=meta.get("tools",[]) or service.tools(cid)
        lines=[f"App: {row.get('display_name')}",f"Status: {row.get('status')}",f"Transport: {meta.get('transport','unknown')}",f"Endpoint: {meta.get('endpoint') or meta.get('command','')}",f"Last error: {row.get('last_error') or 'None'}","",f"Tools ({len(tools)}):"]
        lines.extend(f"• {t.get('name')}: {t.get('description','')}" for t in tools)
        if row.get("last_error"):lines.extend(["","Troubleshooting:",self._friendly_error(row["last_error"])])
        self.details.setPlainText("\n".join(lines))
    @staticmethod
    def _friendly_error(error):
        if "MCP SDK is missing" in error or "No module named 'mcp'" in error:return "Install MCP support using the button below, then restart JARVIS."
        if "FileNotFoundError" in error:return "Command not found. Install the server runtime and verify executable PATH."
        if "timed out" in error.lower():return "Server did not respond. Check command, network and logs."
        return error
    @staticmethod
    def _slug(value):return "-".join(value.strip().lower().split()) or "app"
    def add_custom(self):
        name,ok=QInputDialog.getText(self,"Add STDIO MCP server","Display name:")
        if not ok or not name.strip():return
        command,ok=QInputDialog.getText(self,"STDIO MCP server","Executable (npx, uvx, python):")
        if not ok or not command.strip():return
        args,ok=QInputDialog.getText(self,"STDIO MCP server","Arguments (JSON string array):",text="[]")
        if not ok:return
        try:
            parsed=json.loads(args)
            if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):raise ValueError("Arguments must be a JSON array of strings.")
        except Exception as exc:QMessageBox.warning(self,"Invalid arguments",str(exc));return
        d=AppDescriptor(id="custom-"+self._slug(name),name=name.strip(),description="Custom MCP server",source="user",transport=TransportType.STDIO,command=command.strip(),args=parsed);self._connect_descriptor(d)
    def add_http(self):
        name,ok=QInputDialog.getText(self,"Add MCP HTTP server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP HTTP server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        token,ok=QInputDialog.getText(self,"Authentication","Bearer token (optional):",QLineEdit.EchoMode.Password)
        if not ok:return
        d=AppDescriptor(id="http-"+self._slug(name),name=name.strip(),description="Custom MCP HTTP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip());headers={"Authorization":f"Bearer {token}"} if token.strip() else {};self._connect_http_descriptor(d,headers=headers)
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
    def connect_selected(self):
        cid=self.selected_id()
        if cid:self._busy(True,"Reconnecting…");w=_Worker(lambda:service.reconnect(cid));w.signals.finished.connect(self._connected);self.pool.start(w)
    def _connected(self,row,error):
        self._busy(False)
        if error:
            msg=self._friendly_error(str(error));self.status.setText(f"Connection failed: {msg}");QMessageBox.warning(self,"Connection failed",msg)
        elif row:
            count=len(row.get("metadata",{}).get("tools",[]));self.status.setText(f"{row.get('display_name')}: {row.get('status')} · {count} tools discovered")
            if row.get("last_error"):QMessageBox.warning(self,"Connection failed",self._friendly_error(row["last_error"]))
        self.refresh()
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
