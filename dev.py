from pycaw.pycaw import AudioUtilities

value = 100  # Громкость в процентах

# 1. Получаем устройство
device = AudioUtilities.GetSpeakers()

# 2. Просто устанавливаем громкость в процентах
device.volume_percent = value

print(f"Громкость установлена на {value}%")
