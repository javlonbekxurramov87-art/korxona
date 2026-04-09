import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order

# Telegram Bot sozlamalari
BOT_TOKEN = '6325207843:AAHJ8DeIEoxSIIc6iQJXbthIqfcm1tssxg0'

def send_telegram_msg(chat_id, text):
    """Telegramga xabar yuborish uchun yordamchi funksiya"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, data=payload)
        return response.json()
    except Exception as e:
        print(f"Telegram xatolik: {e}")

@receiver(post_save, sender=Order)
def order_status_notifier(sender, instance, **kwargs):
    if instance.customer_chat_id:
        # Statuslar lug'ati
        status_dictionary = {
            'KIRITILDI': "qabul qilindi va navbatga qo'yildi 📝",
            'TASDIQLANDI': "menejer tomonidan tasdiqlandi ✅",
            'RAD_ETILDI': "rad etildi ❌",
            'USTA_QABUL_QILDI': "usta tomonidan qabul qilindi 👨‍🔧",
            'USTA_BOSHLA': "ishlab chiqarilishi boshlandi 🚀",
            'ISHDA': "hozirda ishlab chiqarish jarayonida ⚙️",
            'USTA_TUGATDI': "tayyor bo'ldi va usta ishini yakunladi 🏁",
            'TAYYOR': "omborda yuklanishga tayyor holatda turibdi 📦",
            'BAJARILDI': "moshinaga ortildi va manzilga jo'natildi 🚚"
        }

        holat_matni = status_dictionary.get(instance.status, instance.status)

        # Xabarni shakllantirish
        xabar = (
            f"Hurmatli **{instance.customer_name}**!\n\n"
            f"Sizning **№{instance.order_number}** buyurtmangiz holati: "
            f"*{holat_matni}*."
        )

        # AGAR MOSHINA RAQAMI BO'LSA (Ayniqsa 'BAJARILDI' yoki 'TAYYOR' holatida)
        # Eslatma: Modelingizda car_number maydoni borligini tekshiring
        if hasattr(instance, 'car_number') and instance.car_number:
            xabar += f"\n\n🚛 **Yuk ortilgan moshina raqami:** `{instance.car_number}`"
        elif instance.status == 'BAJARILDI':
            xabar += f"\n\n🚛 **Moshina raqami:** Tez orada ma'lum qilinadi."

        # Telegramga yuborish
        token = '7980001420:AAFEHQ2g_E6hkBbe0bT3Ea5WiO0eKIWUUkg'
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": instance.customer_chat_id, 
            "text": xabar, 
            "parse_mode": "Markdown"
        }
        
        try:
            import requests
            requests.post(url, data=data)
        except Exception as e:
            print(f"Telegram yuborishda xato: {e}")
    """
    Buyurtma saqlanganda (status o'zgarganda) mijozga xabar yuboradi
    """
    # Agar mijoz botdan ro'yxatdan o'tmagan bo'lsa, xabar yubormaydi
    if not instance.customer_chat_id:
        return

    # Statuslarga mos o'zbekcha matnlar
    status_uz = {
        'KIRITILDI': "✅ Buyurtmangiz qabul qilindi.",
        'TASDIQLANDI': "📑 Menejer buyurtmangizni tasdiqladi.",
        'RAD_ETILDI': "❌ Uzr, buyurtmangiz rad etildi.",
        'USTA_QABUL_QILDI': "👨‍🔧 Usta buyurtmani qabul qilib oldi.",
        'USTA_BOSHLA': "🚀 Usta ishni boshladi.",
        'ISHDA': "⚙️ Buyurtmangiz ishlab chiqarish jarayonida.",
        'USTA_TUGATDI': "🏁 Usta o'z ishini yakunladi.",
        'TAYYOR': "📦 Buyurtmangiz tayyor va omborda yuklanishni kutmoqda!",
        'BAJARILDI': "🚚 Buyurtma yakunlandi va manzilga jo'natildi!"
    }

    current_status_text = status_uz.get(instance.status, instance.status)
    
    # Xabar matni
    message = (
        f"🔔 **Buyurtma Holati Yangilandi!**\n\n"
        f"👤 **Mijoz:** {instance.customer_name}\n"
        f"🔢 **Buyurtma №:** {instance.order_number}\n"
        f"📊 **Hozirgi holat:** *{current_status_text}*\n"
    )

    # Agar usta izoh qoldirgan bo'lsa
    if instance.worker_comment:
        message += f"\n💬 **Usta izohi:** {instance.worker_comment}"

    # Agar ish yakunlanib moshina rasmlari yoki raqami kiritilgan bo'lsa
    # (Modelda car_number maydoni bor deb hisoblaymiz, sizda tepada bor edi)
    # message += f"\n🚛 **Moshina:** {instance.car_number}" 

    send_telegram_msg(instance.customer_chat_id, message)
