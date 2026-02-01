import random

import pyttsx3

JOKES = [
    "Почему программисты любят кофе? Потому что без него код не работает.",
    "Баг — это не ошибка, это неожиданная функция.",
    "Я не ленивый, я в режиме энергосбережения."
]


def tell_joke(*args, **kwargs):
    joke = random.choice(JOKES)
    pyttsx3.speak(joke)
