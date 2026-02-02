import re
from rapidfuzz import fuzz
from core import config


class CommandRouter:

    def __init__(self, commands: dict, threshold: int):
        self.commands = commands
        self.threshold = threshold

    def normalize(self, text: str) -> str:

        text = text.lower()
        text = text.replace("ё", "е")

        # remove garbage
        text = re.sub(r"[^\w\s]", " ", text)

        # remove filler words
        # TODO: add more filter words
        for f in config.FILTERS:
            text = text.replace(f, "")

        return re.sub(r"\s+", " ", text).strip()

    def split_phrases(self, text):

        parts = [text]

        for sep in config.SEPARATORS:
            new = []
            for p in parts:
                new.extend(p.split(sep))
            parts = new

        return [p.strip() for p in parts if len(p.strip()) > 2]

    def detect(self, user_text):

        normalized_text = self.normalize(user_text)
        phrases = self.split_phrases(normalized_text)

        detected = []

        for phrase in phrases:

            best_score = 0
            best_action = None

            for variants, action in self.commands.items():

                for variant in variants:

                    score = fuzz.token_set_ratio(phrase, self.normalize(variant))

                    if score > best_score:
                        best_score = score
                        best_action = action

            if best_score >= self.threshold:
                detected.append((best_action, best_score, phrase))

        return detected
