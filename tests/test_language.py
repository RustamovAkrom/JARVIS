import core.language as language

class FakeIdentifier:
    def __init__(self, ranked): self.ranked=ranked
    def rank(self, text): return self.ranked


def test_written_confidence_and_margin(monkeypatch):
    monkeypatch.setattr(language,"_identifier",FakeIdentifier([("ru",0.8),("en",0.5)]))
    monkeypatch.setattr(language,"_failed",False)
    assert language.written("Привет, как дела?")=="ru"
    monkeypatch.setattr(language,"_identifier",FakeIdentifier([("ru",0.5),("en",0.45)]))
    assert language.written("Привет")==""

def test_written_ignores_links_and_numbers(monkeypatch):
    monkeypatch.setattr(language,"_identifier",None)
    monkeypatch.setattr(language,"_failed",False)
    assert language.written("https://example.com 12345")==""

def test_instruction_uses_detected_code(monkeypatch):
    monkeypatch.setattr(language,"written",lambda text:"uz")
    result=language.instruction("Salom, JARVIS")
    assert "language code 'uz'" in result
    assert "explicitly requests another language" in result
