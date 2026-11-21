"""Connected Apps settings UI for custom MCP stdio and HTTP servers."""
from __future__ import annotations
import json
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QTextEdit,QListWidget,QListWidgetItem,QMessageBox,QInputDialog,QLineEdit
from core.connected_apps.models import AppDescriptor,TransportType
from core.connected_apps.registry import registry
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
        root=QVBoxLayout(self);root.setSpacing(12)
        title=QLabel("CONNECTED APPS");title.setStyleSheet("font-size:18px;font-weight:700;letter-spacing:1px")
        subtitle=QLabel("Connect external services and MCP servers. Connections are optional and isolated from JARVIS core.")
        subtitle.setWordWrap(True);root.addWidget(title);root.addWidget(subtitle)
        row=QHBoxLayout();self.search=QLineEdit();self.search.setObjectName("connected_apps_search")
        self.search.setPlaceholderText("Search connected apps…");self.search.textChanged.connect(self.refresh);row.addWidget(self.search,1)
        add=QPushButton("+ Add STDIO");add.clicked.connect(self.add_custom)
        http=QPushButton("+ Add HTTP");http.clicked.connect(self.add_http);row.addWidget(add);row.addWidget(http);root.addLayout(row)
        self.list=QListWidget();self.list.setMinimumHeight(180);self.list.itemSelectionChanged.connect(self.show_selected);root.addWidget(self.list)
        self.details=QTextEdit();self.details.setReadOnly(True);self.details.setMinimumHeight(110);root.addWidget(self.details)
        buttons=QHBoxLayout();self.connect_btn=QPushButton("Connect / Reconnect");self.connect_btn.clicked.connect(self.connect_selected)
        self.disconnect_btn=QPushButton("Disconnect");self.disconnect_btn.clicked.connect(self.disconnect_selected)
        self.remove_btn=QPushButton("Remove");self.remove_btn.clicked.connect(self.remove_selected)
        buttons.addWidget(self.connect_btn);buttons.addWidget(self.disconnect_btn);buttons.addWidget(self.remove_btn);root.addLayout(buttons)
        self.status=QLabel("Ready");self.status.setWordWrap(True);root.addWidget(self.status);self.refresh()

    def refresh(self,*_):
        query=self.search.text().casefold().strip();selected=self.selected_id();self.list.clear()
        for row in service.connections():
            if query and query not in (row.get("display_name","")+" "+row.get("app_id","")).casefold():continue
            item=QListWidgetItem(f"{row.get('display_name')} · {row.get('status','unknown')}")
            item.setData(Qt.ItemDataRole.UserRole,row.get("id"));self.list.addItem(item)
            if selected and item.data(Qt.ItemDataRole.UserRole)==selected:self.list.setCurrentItem(item)
        self.show_selected()

    def selected_id(self):
        item=self.list.currentItem();return item.data(Qt.ItemDataRole.UserRole) if item else None
    def show_selected(self):
        cid=self.selected_id();row=service.store.get_connection(cid) if cid else None
        if not row:self.details.setPlainText("Select a connection to inspect status and tools.");return
        tools=service.tools(cid);meta=row.get("metadata",{})
        lines=[f"App: {row.get('display_name')}",f"Status: {row.get('status')}",
               f"Transport: {meta.get('transport','unknown')}",f"Endpoint: {meta.get('endpoint') or meta.get('command','')}",
               f"Last error: {row.get('last_error') or 'None'}","Tools:"]
        lines.extend(f"  • {t.get('name')}: {t.get('description','')}" for t in tools)
        self.details.setPlainText("\n".join(lines))

    def add_custom(self):
        name,ok=QInputDialog.getText(self,"Add MCP Server","Display name:")
        if not ok or not name.strip():return
        command,ok=QInputDialog.getText(self,"MCP Server","Executable / command:")
        if not ok or not command.strip():return
        args,ok=QInputDialog.getText(self,"MCP Server","Arguments (JSON array):",text="[]")
        if not ok:return
        try:
            parsed=json.loads(args)
            if not isinstance(parsed,list) or not all(isinstance(x,str) for x in parsed):raise ValueError("Arguments must be a JSON string array")
        except Exception as exc:QMessageBox.warning(self,"Invalid arguments",str(exc));return
        descriptor=AppDescriptor(id="custom-"+name.strip().lower().replace(" ","-"),name=name.strip(),
            description="Custom MCP server",source="user",transport=TransportType.STDIO,command=command.strip(),args=parsed)
        self._connect_descriptor(descriptor)

    def add_http(self):
        name,ok=QInputDialog.getText(self,"Add MCP HTTP Server","Display name:")
        if not ok or not name.strip():return
        endpoint,ok=QInputDialog.getText(self,"MCP HTTP Server","Server URL (HTTP(S)):")
        if not ok or not endpoint.strip():return
        descriptor=AppDescriptor(id="http-"+name.strip().lower().replace(" ","-"),name=name.strip(),
            description="Custom MCP HTTP server",source="user",transport=TransportType.HTTP,endpoint=endpoint.strip())
        self._connect_http_descriptor(descriptor)

    def _connect_http_descriptor(self,descriptor,cid=None):
        self.status.setText(f"Connecting to {descriptor.name}…")
        worker=_Worker(lambda:service.connect_http(descriptor,connection_id=cid))
        worker.signals.finished.connect(self._connected);self.pool.start(worker)
    def _connect_descriptor(self,descriptor,cid=None):
        self.status.setText(f"Connecting to {descriptor.name}…")
        worker=_Worker(lambda:service.connect_stdio(descriptor,connection_id=cid))
        worker.signals.finished.connect(self._connected);self.pool.start(worker)

    def connect_selected(self):
        cid=self.selected_id();row=service.store.get_connection(cid) if cid else None
        if not row:return
        meta=row.get("metadata",{})
        descriptor=registry.get(row.get("app_id"))
        if not descriptor:
            if meta.get("transport")=="http" and meta.get("endpoint"):
                descriptor=AppDescriptor(id=row["app_id"],name=row["display_name"],source="stored",
                    transport=TransportType.HTTP,endpoint=meta["endpoint"])
            elif meta.get("command"):
                descriptor=AppDescriptor(id=row["app_id"],name=row["display_name"],source="stored",
                    transport=TransportType.STDIO,command=meta["command"],args=meta.get("args",[]))
            else:
                QMessageBox.information(self,"Reconnect","Saved server configuration is unavailable.");return
        if descriptor.transport==TransportType.HTTP:self._connect_http_descriptor(descriptor,cid)
        else:self._connect_descriptor(descriptor,cid)

    def _connected(self,row,error):
        self.status.setText(f"Connection failed: {error}" if error else
            f"{row.get('display_name')}: {row.get('status')} · {len(row.get('metadata',{}).get('tools',[]))} tools")
        self.refresh()
    def disconnect_selected(self):
        cid=self.selected_id()
        if not cid:return
        worker=_Worker(lambda:service.disconnect(cid));worker.signals.finished.connect(lambda result,error:self._operation_done("Disconnected",error));self.pool.start(worker)
    def remove_selected(self):
        cid=self.selected_id()
        if not cid:return
        if QMessageBox.question(self,"Remove connection","Remove this connection and stored credentials?")!=QMessageBox.StandardButton.Yes:return
        worker=_Worker(lambda:service.remove(cid));worker.signals.finished.connect(lambda result,error:self._operation_done("Removed",error));self.pool.start(worker)
    def _operation_done(self,message,error):
        self.status.setText(message if not error else f"Error: {error}");self.refresh()
