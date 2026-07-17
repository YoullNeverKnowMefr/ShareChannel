from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main():
    print("=" * 60)
    print("Создание Telethon Session String")
    print("=" * 60)
    print()
    print("Получите API_ID и API_HASH на https://my.telegram.org")
    print()
    
    api_id = input("Введите API_ID: ").strip()
    api_hash = input("Введите API_HASH: ").strip()
    phone = input("Введите номер телефона (например +79991234567): ").strip()
    
    if not phone.startswith("+"):
        phone = "+" + phone

    print("\nСоздание сессии...")
    print("Вам придет код в Telegram. Введите его когда попросят.\n")

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()
        
        print()
        print("=" * 60)
        print("✅ СЕССИЯ УСПЕШНО СОЗДАНА!")
        print("=" * 60)
        print()
        print("Ваш SESSION_STRING:")
        print("-" * 60)
        print(session_string)
        print("-" * 60)
        print()
        print("📋 Как использовать:")
        print()
        print("Вариант 1 (Legacy - один аккаунт):")
        print("  Добавьте в .env файл:")
        print("  TELETHON_SESSION_STRING=<строка выше>")
        print()
        print("Вариант 2 (Рекомендуется - пул аккаунтов):")
        print("  1. Запустите бота")
        print("  2. Перейдите: Безопасность → Telethon аккаунты → Добавить")
        print("  3. Введите данные:")
        print(f"     - Телефон: {phone}")
        print(f"     - API ID: {api_id}")
        print(f"     - API Hash: {api_hash}")
        print("     - Session String: <строка выше>")
        print()
        print("При использовании нескольких аккаунтов система автоматически")
        print("переключится на другой аккаунт при FloodWait!")
        print("=" * 60)


if __name__ == "__main__":
    main()
