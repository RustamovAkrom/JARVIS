# import pyttsx3


# class TextToSpeach:

#     def __init__(self, rate=170, volume=1.0):

#         self.engine = pyttsx3.init()
#         self.engine.setProperty("voice", "ru")
#         self.engine.setProperty("pitch", 75)
#         self.engine.setProperty("rate", rate)
#         self.engine.setProperty("volume", volume)


#     def say(self, text):
#         if not text:
#             return

#         self.engine.say(text)
#         self.engine.runAndWait()
#         self.engine.stop()
