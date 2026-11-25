from unittest.mock import Mock,patch
from plugins import connected_apps

def test_list_apps_returns_connections():
    rows=[{"id":"c1","display_name":"Demo","status":"connected"}]
    with patch("core.connected_apps.service.service.connections",return_value=rows):
        result=connected_apps.run({"action":"list_apps"})
    assert "connected_apps.list" in result
    assert "Demo" in result

def test_list_tools_filters_query():
    tools=[{"name":"search","description":"Search files","app":"Drive","app_id":"drive","connection_id":"c1","input_schema":{}},
           {"name":"delete","description":"Delete file","app":"Drive","app_id":"drive","connection_id":"c1","input_schema":{}}]
    with patch("core.connected_apps.agent.agent.find_tools",return_value=[tools[0]]):
        result=connected_apps.run({"action":"list_tools","query":"search"})
    assert "search" in result

def test_execute_rejects_unknown_tool():
    with patch("core.connected_apps.agent.agent.describe_tools",return_value=[]):
        result=connected_apps.run({"action":"execute","connection_id":"c1","tool":"unknown","arguments":{}})
    assert "failed" in result.lower()

def test_connect_routes_to_settings_panel():
    player=Mock()
    result=connected_apps.run({"action":"connect","app":"GitHub"},player=player)
    player.open_panel.assert_called_once_with("apps",{"search":"GitHub"})
    assert "connected_apps.connect" in result
