# orders/models.py
import uuid
from django.db import models
from django.contrib.auth import get_user_model 
from django.conf import settings
from decimal import Decimal
from django.utils import timezone
import qrcode
from PIL import Image
from io import BytesIO
from django.core.files import File  # MANA SHU QATORNI QO'SHING
from django.urls import reverse
User = get_user_model() 

# =======================================================================
# 1. KATEGORIYA MODELI
# =======================================================================
class Category(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="Kategoriya Nomi")
    description = models.TextField(blank=True, verbose_name="Izoh")
    # created_at = models.DateTimeField(auto_now_add=True)
    # models.py ichida
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Kategoriya"
        verbose_name_plural = "Kategoriyalar"
        ordering = ['name']

    def __str__(self):
        return self.name

# =======================================================================
# 2. WORKER/USTA MODELI
# =======================================================================
class Worker(models.Model):
    ROLE_CHOICES = [
        ('PANEL', "Panel Ustasi"),
        ('LIST', "List Ustasi"),
        ('ESHIK', "Eshik Ustasi"),
        ('UGOL', "Ugol Ustasi"),
        ('LIST_ESHIK', "List va Eshik ustalari"),
    ]

    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='worker_profile', 
        verbose_name="Foydalanuvchi"
    )
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, verbose_name="Usta Roli")

    class Meta:
        verbose_name = "Usta"
        verbose_name_plural = "Ustalar"

    def __str__(self):
        username = getattr(self.user, 'username', 'Nomaʼlum foydalanuvchi')
        return f"{username} - {self.get_role_display()}"

# =======================================================================
# 3. MATERIAL MODELI
# =======================================================================
class Material(models.Model):
    UNIT_CHOICES = [
        ('kg', 'Kilogramm (kg)'),
        ('m2', 'Kvadrat Metr (m²)'),
        ('son', 'Dona / Son (ta)'),
        ('m', 'Metr (m)'),
        ('litr', 'Litr'),
    ]
    
    name = models.CharField(max_length=255, verbose_name="Material nomi", unique=True)
    product_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Maxsulot nomi",
        help_text="Ushbu materialdan tayyorlanadigan yoki bog'liq maxsulot nomi"
    )
    category = models.ForeignKey(
        Category, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        verbose_name="Material Kategoriyasi"
    )
    unit = models.CharField(
        max_length=10, 
        choices=UNIT_CHOICES, 
        default='son', 
        verbose_name="O'lchov birligi"
    )
    quantity = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=Decimal('0.000'), 
        verbose_name="Ombordagi joriy qoldiq"
    )
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new or not self.qr_code:
            # FAQAT TOZA URL (Hech qanday "Material:" so'zisiz)
            # Telefoningiz IP-manzilini yozsangiz (masalan 192.168...) telefonda ham ochiladi
            base_url = "http://127.0.0.1:8000" 
            full_url = f"{base_url}/orders/material/output/?material_id={self.id}"

            qr = qrcode.QRCode(version=1, box_size=10, border=1)
            qr.add_data(full_url) # Faqat link
            qr.make(fit=True)

            qr_image = qr.make_image(fill_color="black", back_color="white")
            
            buffer = BytesIO()
            qr_image.save(buffer, format='PNG')
            self.qr_code.save(f'qr-{self.id}.png', File(buffer), save=False)
            
            Material.objects.filter(pk=self.pk).update(qr_code=self.qr_code)
    @property
    def current_stock(self):
        return self.quantity

    price_per_unit = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=Decimal('0.00'), 
        verbose_name="Birlik narxi"
    )
    min_stock_level = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=Decimal('0.000'), 
        verbose_name="Minimal qoldiq"
    )
    max_stock_level = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        default=0, 
        null=True, 
        blank=True,
        verbose_name="Maksimal qoldiq"
    )
    code = models.CharField(max_length=50, unique=True, null=True, blank=True, verbose_name="QR/Shtrix Kod")
    last_updated = models.DateTimeField(auto_now=True, verbose_name="Oxirgi yangilanish")

    class Meta:
        verbose_name = "Material"
        verbose_name_plural = "Materiallar (Omborxona)"
        ordering = ['name']

    def __str__(self):
        base_str = f"{self.name}"
        if self.product_name:
            base_str += f" → {self.product_name}"
        return f"{base_str} (Qoldiq: {self.quantity:,.3f} {self.unit.upper()})"
    
# =======================================================================
# 4. ORDER MODELI
# =======================================================================






class ActiveOrderManager(models.Manager):
    def get_queryset(self):
        # Faqat yakunlanmagan statuslarni qaytaradi
        return super().get_queryset().exclude(status__in=['BAJARILDI', 'TAYYOR'])



from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError

class Order(models.Model):
    STATUS_CHOICES = [
        ('KIRITILDI', "1. Kiritildi (Admin)"),
        ('TASDIQLANDI', "2. Tasdiqlandi (Menejer)"),
        ('RAD_ETILDI', "2. Rad Etildi (Menejer)"), 
        ('USTA_QABUL_QILDI', "3. Usta Qabul Qildi"),
        ('USTA_BOSHLA', "4. Usta Boshladi"),
        ('ISHDA', "5. Ishlab Chiqarishda"),
        ('USTA_TUGATDI', "6. Usta Yakunladi"),
        ('TAYYOR', "7. Tayyor"),
        ('BAJARILDI', "8. Bajarildi") 
    ]

    WORKER_TYPE_CHOICES = [
        ('LIST', 'List Ustasi'),
        ('ESHIK', 'Eshik Ustasi'),
        ('LIST_ESHIK', 'List va Eshik Ustasi'),
        ('PANEL', 'Panel Ustasi'),
        ('UGOL', 'Ugol Ustasi'),
    ]

    PANEL_TYPE_CHOICES = [
        ('PIR', 'PIR Panel'),
        ('PUR', 'PUR Panel'),
    ]

    PANEL_SUBTYPE_CHOICES = [
        ('TOM', 'Tom'),
        ('SECRETPIR', 'SecretPir'),
        ('SOVUTGICH', 'PIR Sovutgich')
    ]

    PANEL_THICKNESS_CHOICES = [
        ('5', '5 sm'),
        ('8', '8 sm'),
        ('10', '10 sm'),
        ('15', '15 sm')
    ]
    def calculate_materials(self):
        """
        Buyurtma uchun ombordagi materiallar hisobini qaytaradi.
        Hozirgi hisob-kitob order.panel_kvadrat asosida oddiy formulaga o‘tkazilgan.
        """
        from decimal import Decimal

        kvadrat = self.panel_kvadrat or Decimal('0')
        if kvadrat == 0:
            return {}

        return {
            'foam_volume': (kvadrat * Decimal('0.20')).quantize(Decimal('0.001')),
            'sheets_area': (kvadrat * Decimal('0.50')).quantize(Decimal('0.001')),
        }
    def get_detailed_calculation(self):
        """
        Buyurtma uchun aniq sarf-xarajat kalkulyatsiyasi
        """
        # Ma'lumotlarni olish
        # panel_kvadrat orqali bo'yini hisoblab olamiz (Eni doim 0.96m deb olingan)
        eni = Decimal('0.96') # 960 mm
        kvadratura = Decimal(str(self.panel_kvadrat))
        boyi_metr = kvadratura / eni
        boyi_mm = boyi_metr * 1000
        qalinlik = Decimal(str(self.panel_thickness or 10))

        # 1. LIST HISOBI (2 tomonga)
        list_sarfi = kvadratura * 2

        # 2. SIRYO HISOBI
        # Formula: (Boyi(mm) * Eni(mm) * Qalinlik * 0.42) / 2.1
        # Eslatma: 2.1 bu zichlik koeffitsienti bo'lishi mumkin
        siryo_sarfi = (boyi_mm * 960 * qalinlik * Decimal('0.42')) / Decimal('2100') 
        # (2.1 kg/m3 formatga moslash uchun 2100 ga bo'ldik)

        # 3. ZAMOK HISOBI
        # Eni bo'yicha doim 4 ta (boshida 2, oxirida 2)
        eni_zamok = 4
        
        # Bo'yi bo'yicha: boshidan 70sm, oxiridan 70sm tashlab, har 960mm da 1 tadan
        # Avval chegaralarni ayiramiz: jami boyi(mm) - 1400mm
        ishchi_boyi = boyi_mm - 1400
        if ishchi_boyi > 0:
            boyi_zamok_soni = (math.floor(ishchi_boyi / 960) + 1) * 2 # 2 tomoni uchun
        else:
            boyi_zamok_soni = 0
            
        jami_zamok = eni_zamok + boyi_zamok_soni

        # 4. STAKANCHIK HISOBI
        # Har bir zamok uchun bittadan yoki siz aytgandek har 8 taga 1 ta? 
        # Odatda har zamokda bitta bo'ladi:
        stakanchik = jami_zamok 

        return {
            'list': round(list_sarfi, 2),
            'siryo': round(siryo_sarfi, 2),
            'zamok': jami_zamok,
            'stakanchik': stakanchik,
            'boyi_m': round(boyi_metr, 2)
        }

    def decrement_stock(self):
        """
        Buyurtma uchun kerakli materiallarni ombordan ayirish.
        Bu funksiya Order modeliga tegishli bo'lishi kerak.
        """
        requirements = self.calculate_materials()
        if not requirements:
            return False

        import decimal

        foam = Material.objects.filter(name__icontains='siryo').first()
        if foam and requirements.get('foam_volume') is not None:
            val = decimal.Decimal(str(requirements['foam_volume']))
            foam.quantity -= val
            foam.save()
            MaterialTransaction.objects.create(
                material=foam,
                transaction_type='OUT',
                quantity_change=val,
                order=self,
                notes=f"Order #{self.order_number} uchun sarflandi"
            )

        sheet = Material.objects.filter(name__icontains='list').first()
        if sheet and requirements.get('sheets_area') is not None:
            val = decimal.Decimal(str(requirements['sheets_area']))
            sheet.quantity -= val
            sheet.save()
            MaterialTransaction.objects.create(
                material=sheet,
                transaction_type='OUT',
                quantity_change=val,
                order=self,
                notes=f"Order #{self.order_number} uchun sarflandi"
            )

        return True

    @property
    def remaining_amount(self):
        """
        Qolgan qarz summasini hisoblash: Jami narx - Zalog
        """
        total = self.total_price or 0
        paid = self.prepayment or 0
        return total - paid
    ESHIK_TURI_CHOICES  = [(f'F{i}', f'F{i}') for i in range(1, 9)]
    PAROG_CHOICES = [('PAROGLI', 'Parogli'), ('PAROGSIZ', 'Parogsiz')]
    DIRECTION_CHOICES = [('ONG', "O'ng"), ('CHAP', 'Chap')]
    objects = models.Manager() # Standart manager
    active = ActiveOrderManager() # Aktiv buyurtmalar uchun
    product_name = models.CharField(max_length=255, verbose_name="Mahsulot nomi", blank=True, null=True)
    worker_comment = models.TextField(blank=True, null=True, verbose_name="Usta izohi")
    worker_started_at = models.DateTimeField(null=True, blank=True, verbose_name="Ish boshlangan vaqt")
    worker_finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Ish yakunlangan vaqt")
    needs_manager_approval = models.BooleanField(default=False, verbose_name="Menejer tasdig'i kerak")
    parent_order = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='sub_orders')
    
    order_number = models.CharField(max_length=50, unique=True, verbose_name="Buyurtma Raqami", editable=False)
    customer_unique_id = models.CharField(max_length=50, verbose_name="Mijoz ID", help_text="Ko'p martalik mijoz identifikatori")
    customer_name = models.CharField(max_length=150, verbose_name="Xaridor Nomi")
    
    worker_type = models.CharField(max_length=15, choices=WORKER_TYPE_CHOICES, default='LIST')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='KIRITILDI')
    customer_chat_id = models.CharField(max_length=100, blank=True, null=True)
    car_number = models.CharField(max_length=50, verbose_name="Moshina raqami", blank=True, null=True)
    trip = models.ForeignKey('DriverTrip', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    trip_number = models.CharField(max_length=100, null=True, blank=True)
    # Eshik parametrlari
    eshik_turi = models.CharField(max_length=255, choices=ESHIK_TURI_CHOICES , blank=True, null=True)
    zamokli_eshik = models.BooleanField(default=False, verbose_name="Zamokli")
    parog_turi = models.CharField(max_length=10, choices=PAROG_CHOICES, blank=True, null=True)
    eshik_yonalishi = models.CharField(max_length=5, choices=DIRECTION_CHOICES, blank=True, null=True)
    balandligi = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    eni = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Panel parametrlari
    panel_type = models.CharField(max_length=10, choices=PANEL_TYPE_CHOICES, blank=True, null=True)
    panel_subtype = models.CharField(max_length=20, choices=PANEL_SUBTYPE_CHOICES, blank=True, null=True)
    panel_thickness = models.CharField(max_length=3, choices=PANEL_THICKNESS_CHOICES, blank=True, null=True)
    panel_kvadrat = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    total_price = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Umumiy Narx")
    prepayment = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Zalog (Oldindan to'lov)")
    pdf_file = models.FileField(upload_to='order_pdfs/', verbose_name="PDF Chizma", blank=True, null=True)
    deadline = models.DateTimeField(null=True, blank=True, verbose_name="Tugallanish muddati")
    
    assigned_workers = models.ManyToManyField('Worker', related_name='assigned_orders', blank=True)
    comment = models.TextField(blank=True, null=True, verbose_name="Admin izohi")
    
    start_image = models.ImageField(upload_to='order_photos/start/', null=True, blank=True)
    finish_image = models.ImageField(upload_to='order_photos/finish/', null=True, blank=True)

    started_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='started_orders'
    )
    work_started_at = models.DateTimeField(null=True, blank=True)
    start_confirmed = models.BooleanField(default=False)  # Telegramga yuborilganligini belgilash

    # Kim tugatdi / qachon
    finished_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='finished_orders'
    )
    work_finished_at = models.DateTimeField(null=True, blank=True)
    finish_confirmed = models.BooleanField(default=False)  # Telegramga yuborilganligini belgilash
    delivery_img_1 = models.ImageField(upload_to='order_photos/delivery/', null=True, blank=True)
    delivery_img_2 = models.ImageField(upload_to='order_photos/delivery/', null=True, blank=True)
    delivery_img_3 = models.ImageField(upload_to='order_photos/delivery/', null=True, blank=True)
    # Optional: agar kerak bo‘lsa log uchun
    start_telegram_sent = models.BooleanField(default=False)
    finish_telegram_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def clean(self):
        super().clean()
        
        # 1. PUR uchun mantiq (Tanlanganda qalinliklari 5, 8, 10, 15 bo'lishi kerak)
        if self.panel_type == 'PUR':
            if self.panel_thickness not in ['5', '8', '10', '15']:
                raise ValidationError("PUR panel uchun faqat 5, 8, 10 yoki 15 sm tanlash mumkin.")

        # 2. PIR uchun mantiq (Tom: 5sm, SecretFix: 5,8sm, Sovutgich: 5,10,15sm)
        if self.panel_type == 'PIR':
            if self.panel_subtype == 'TOM' and self.panel_thickness != '5':
                raise ValidationError("PIR Tom panel uchun faqat 5 sm qalinlik tanlash mumkin.")
            
            if self.panel_subtype == 'SECRETPIR' and self.panel_thickness not in ['5', '8']:
                raise ValidationError("PIR SecretFix uchun faqat 5 yoki 8 sm qalinlik tanlash mumkin.")
            
            if self.panel_subtype == 'SOVUTGICH' and self.panel_thickness not in ['5', '10', '15']:
                raise ValidationError("PIR Sovutgich uchun faqat 5, 10 yoki 15 sm qalinlik tanlash mumkin.")

        # 3. Eshik yoki LIST_ESHIK (Universal) mantiqi
        if self.worker_type in ['ESHIK', 'LIST_ESHIK']:
            if not self.eshik_turi:
                raise ValidationError("Eshik turi tanlanishi shart.")
            if not self.parog_turi:
                raise ValidationError("Parog turi tanlanishi shart.")
            if not self.eshik_yonalishi:
                raise ValidationError("Eshik yo'nalishi (o'ng/chap) tanlanishi shart.")
            if self.balandligi is None or self.eni is None:
                raise ValidationError("Eshik/Prayom o'lchamlari (balandlik va eni) kiritilishi shart.")
            # Eshik qalinligi tekshiruvi
            # TO‘G‘RISI — eshik_qalinligi tekshiriladi
            # if self.eshik_qalinligi not in ['5', '8', '10', '15']:
            #     raise ValidationError({
            #         "eshik_qalinligi": "Eshik qalinligi uchun faqat 5, 8, 10 yoki 15 sm tanlash mumkin."
            #     })


    def save(self, *args, **kwargs):
        # 1. Order Number yaratish
        if not self.order_number:
            today = timezone.now()
            year_prefix = today.strftime("%Y")
            last_order = Order.objects.filter(order_number__startswith=f"ORD-{year_prefix}").order_by('-id').first()
            num = (int(last_order.order_number.split('-')[-1]) + 1) if last_order else 1
            self.order_number = f"ORD-{year_prefix}-{num:04d}"

        old_status = Order.objects.filter(pk=self.pk).values_list('status', flat=True).first() if self.pk else None
        should_create_next = (self.status == 'USTA_TUGATDI' and old_status != 'USTA_TUGATDI')

        super().save(*args, **kwargs)

        if should_create_next and not Order.objects.filter(parent_order=self).exists():
            next_worker_type = None
            if self.worker_type in ['LIST', 'ESHIK', 'LIST_ESHIK']:
                next_worker_type = 'PANEL'
            elif self.worker_type == 'PANEL':
                next_worker_type = 'UGOL'

            if next_worker_type:
                from .models import Worker
                
                # 1. Yangi sub-order (child) yaratish
                new_order = Order.objects.create(
                    customer_unique_id=self.customer_unique_id,
                    customer_name=self.customer_name,
                    product_name=f"{self.product_name} ({next_worker_type})",
                    worker_type=next_worker_type,
                    parent_order=self,
                    panel_type=self.panel_type,
                    panel_subtype=self.panel_subtype,
                    panel_thickness=self.panel_thickness,
                    panel_kvadrat=self.panel_kvadrat,
                    eshik_turi=self.eshik_turi,
                    pdf_file=self.pdf_file,
                    # SHU YERNI O'ZGARTIRDIK:
                    status='TASDIQLANDI',  # Child uchun menejer tasdig'i shart emas
                    created_by=self.created_by
                )

                # 2. Ustalarni topish va biriktirish
                target_workers = Worker.objects.filter(role=next_worker_type)
                
                if target_workers.exists():
                    new_order.assigned_workers.add(*target_workers)
                    
                    # 3. Child ustalarga bildirishnoma yuborish (ixtiyoriy)
                    # Bu orqali usta o'z telefoniga yoki paneliga xabar oladi
                    for worker in target_workers:
                        if worker.user:
                            from .models import Notification # Agar model boshqa joyda bo'lsa
                            Notification.objects.create(
                                user=worker.user,
                                order=new_order,
                                message=f"Yangi vazifa: №{new_order.order_number} ({next_worker_type}). Ishni boshlashingiz mumkin!"
                            )

from django.db import models
from django.contrib.auth.models import User
from django.db import models
from django.contrib.auth.models import User

class GuardPatrol(models.Model):
    guard = models.ForeignKey(User, on_delete=models.CASCADE)
    checkpoint_name = models.CharField(max_length=100) # Masalan: "Asosiy darvoza"
    patrol_time_slot = models.CharField(max_length=50) # Masalan: "05:00 - 05:20"
    image1 = models.ImageField(upload_to='patrol/%Y/%m/%d/')
    image2 = models.ImageField(upload_to='patrol/%Y/%m/%d/')
    image3 = models.ImageField(upload_to='patrol/%Y/%m/%d/')
    image4 = models.ImageField(upload_to='patrol/', null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.guard.username} - {self.patrol_time_slot}"
# =======================================================================
# 5. MATERIAL TRANSACTION MODELI
# =======================================================================
class MaterialTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('IN', 'Kirim (Omborga kirish)'),
        ('OUT', 'Chiqim (Ombordan chiqish/Sarflanish)'),
    ]

    material = models.ForeignKey(
        Material, 
        on_delete=models.PROTECT, 
        verbose_name="Material nomi"
    )
    transaction_type = models.CharField(
        max_length=3, 
        choices=TRANSACTION_TYPES, 
        verbose_name="Harakat turi"
    )
    quantity_change = models.DecimalField(
        max_digits=10, 
        decimal_places=3, 
        verbose_name="Miqdordagi o'zgarish"
    )
    transaction_barcode = models.CharField(
        max_length=100, 
        unique=True, 
        null=True, 
        blank=True, 
        verbose_name="Partiya Barcode"
    )
    received_by = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="Qabul qiluvchi shaxs/ustaxona"
    )
    order = models.ForeignKey(
        Order, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Bog'liq buyurtma"
    )
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Amalga oshirdi"
    )
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Vaqti")
    notes = models.TextField(
        null=True, 
        blank=True, 
        verbose_name="Izoh/Sabab"
    )

    class Meta:
        verbose_name = "Material harakati"
        verbose_name_plural = "Material harakatlari (Tranzaksiyalar)"
        ordering = ['-timestamp']

    def save(self, *args, **kwargs):
        # 1. Barcode faqat bo'sh bo'lsa va faqat KIRIM bo'lsa yaratilishi kerak
        if not self.transaction_barcode and self.transaction_type == 'IN':
            # Material nomidan xavfsiz foydalanish (probel va belgilarni tozalash)
            import re
            prefix = re.sub(r'[^a-zA-Z0-9]', '', self.material.name)[:3].upper() if self.material else "MTR"
            
            # Unikal id qo'shish
            unique_id = uuid.uuid4().hex[:6].upper()
            self.transaction_barcode = f"{prefix}-{unique_id}"
        
        # 2. Agar Chiqim (OUT) bo'lsa, barcodeni null saqlash yoki 
        # chiqim qilingan partiya kodini qo'lda kiritishni talab qilish mumkin.
        
        super().save(*args, **kwargs)

    def __str__(self):
        # Miqdor yoniga birligini ham qo'shib qo'ysak, adminga oson bo'ladi
        unit = self.material.unit if self.material else ""
        return f"[{self.get_transaction_type_display()}] {self.material.name}: {self.quantity_change} {unit}"

# =======================================================================
# 6. NOTIFICATION MODELI
# =======================================================================
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name="Qabul qiluvchi foydalanuvchi")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Tegishli buyurtma")
    message = models.CharField(max_length=255, verbose_name="Xabar matni")
    is_read = models.BooleanField(default=False, verbose_name="O'qilgan")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Xabarnoma"
        verbose_name_plural = "Xabarnomalar"

    def __str__(self):
        return f"{self.user.username}: {self.message[:30]}..."
    


from django.db import models
import string, random

class Customer(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    unique_id = models.CharField(max_length=10, unique=True, editable=False)

    def save(self, *args, **kwargs):
        if not self.unique_id:
            # 6 raqamli unik ID yaratish
            self.unique_id = ''.join(random.choices(string.digits, k=6))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.unique_id})"
    

class DriverTrip(models.Model):
    driver = models.ForeignKey(User, on_delete=models.CASCADE)
    car_number = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True) # Reys ochiq/yopiq
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)

class TripPoint(models.Model):
    trip = models.ForeignKey(DriverTrip, on_delete=models.CASCADE, related_name='points')
    latitude = models.FloatField()
    longitude = models.FloatField()
    is_stop = models.BooleanField(default=False) # Bu to'xtash nuqtasi (B nuqta)
    stop_duration = models.DurationField(null=True, blank=True) # Qancha kutgani
    timestamp = models.DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product_name = models.CharField(max_length=100) # Стеновой, Кровельный, Дверь
    product_type = models.CharField(max_length=50)  # F1..F8 yoki 80mm, 100mm
    length = models.FloatField(default=0)
    quantity = models.IntegerField(default=0)
    area = models.FloatField(default=0)
    price = models.FloatField(default=0)
    total_sum = models.FloatField(default=0)
from django.db import models
from django.contrib.auth.models import User

class MaterialOutput(models.Model):
    """Material chiqarish modeli"""
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='outputs')
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    reason = models.TextField(blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Material chiqarish"
        verbose_name_plural = "Material chiqarishlar"
    
    def __str__(self):
        return f"{self.material.name} - {self.quantity} {self.material.unit} ({self.created_at.strftime('%d.%m.%Y')})"

# orders/models.py ichida
class OrderHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='history')
    status = models.CharField(max_length=50)
    updated_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.order.order_number} - {self.status} ({self.updated_at.strftime('%d.%m.%Y %H:%M')})"
    

from telegram import Bot

BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

def send_telegram_notification(order: "Order", message: str):
    """Buyurtma statusi o'zgarganda Telegramga yuboradi."""
    bot = Bot(token=BOT_TOKEN)
    try:
        bot.send_message(chat_id=order.customer_unique_id, text=message)
    except Exception as e:
        print(f"Telegramga xabar yuborilmadi: {e}")

# Order modelining save metodida
def save(self, *args, **kwargs):
    old_status = None
    if self.pk:
        old_status = Order.objects.get(pk=self.pk).status

    super().save(*args, **kwargs)

    # Status o‘zgarganda yuborish
    if old_status != self.status:
        msg = (
            f"Sizning buyurtmangiz №{self.order_number} holati o‘zgardi:\n"
            f"Yangi status: {self.get_status_display()}"
        )
        send_telegram_notification(self, msg)

import requests
from django.db.models.signals import post_save
from django.dispatch import receiver

# Xabar yuborish funksiyasi
def notify_customer(chat_id, status, customer_name, car_number):
    token = '7980001420:AAFEHQ2g_E6hkBbe0bT3Ea5WiO0eKIWUUkg'
    status_uz = {
        'KIRITILDI': "navbatga qo'yildi ⏳",
        'ISHDA': "ishlab chiqarishga tushdi 🛠",
        'USTA_TUGATDI': "usta tomonidan tugatildi va omborga yo'llandi 📦",
        'TAYYOR': "tayyor bo'ldi va yuklanishni kutmoqda ✅",
        'BAJARILDI': f"moshinaga ortildi va yo'lga chiqdi 🚚. Moshina raqami: {car_number}"
    }
    
    xabar = f"Hurmatli {customer_name}, buyurtmangiz hozirgina {status_uz.get(status, status)}."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={'chat_id': chat_id, 'text': xabar})

# Django signali: Har safar "Save" bosilganda ishga tushadi
@receiver(post_save, sender=Order)
def order_status_notifier(sender, instance, **kwargs):
    if instance.customer_chat_id: # Agar mijoz botdan ro'yxatdan o'tgan bo'lsa
        notify_customer(
            instance.customer_chat_id, 
            instance.status, 
            instance.customer_name, 
            instance.car_number
        )
# constructor/models.py
from django.db import models
from django.contrib.auth.models import User


class Project(models.Model):
    """Loyiha - sovutish kamerasi buyurtmasi"""
    
    # Asosiy ma'lumotlar
    project_name = models.CharField(max_length=200, blank=True, verbose_name="Loyiha nomi")
    room_code = models.CharField(max_length=50, default="EP-001", verbose_name="Loyiha kodi")
    
    # O'lchamlar (metr)
    length_m = models.DecimalField(max_digits=6, decimal_places=2, default=5.0, verbose_name="Uzunlik (m)")
    width_m = models.DecimalField(max_digits=6, decimal_places=2, default=4.0, verbose_name="Eni (m)")
    height_m = models.DecimalField(max_digits=6, decimal_places=2, default=3.0, verbose_name="Balandlik (m)")
    
    # Devor
    wall_type = models.CharField(max_length=50, default="Sovutgich (PIR)", verbose_name="Devor turi")
    wall_thickness = models.CharField(max_length=10, default="100mm", verbose_name="Devor qalinligi")
    
    # Patalok (Shift)
    ceiling_type = models.CharField(max_length=50, default="Sovutgich (PIR)", verbose_name="Patalok turi")
    ceiling_thickness = models.CharField(max_length=10, default="80mm", verbose_name="Patalok qalinligi")
    
    # Pol
    has_floor = models.BooleanField(default=True, verbose_name="Pol paneli bormi?")
    floor_type = models.CharField(max_length=50, blank=True, default="PIR (Standart)", verbose_name="Pol turi")
    floor_thickness = models.CharField(max_length=10, blank=True, default="100mm", verbose_name="Pol qalinligi")
    
    # Panel
    panel_width = models.DecimalField(max_digits=5, decimal_places=2, default=1.16, verbose_name="Panel ishchi eni (m)")
    
    # Eshik
    door_type = models.CharField(max_length=50, default="Muzlatkich eshigi", verbose_name="Eshik turi")
    door_side = models.CharField(max_length=20, default="Old", verbose_name="Eshik joyi")
    door_position = models.CharField(max_length=20, default="O'rta", verbose_name="Eshik pozitsiyasi")
    door_opening = models.CharField(max_length=20, default="Ichkariga", verbose_name="Eshik ochilishi")
    
    # Agregat
    unit_type = models.CharField(max_length=50, default="Split-sistema (Nizkotemp)", verbose_name="Agregat turi")
    unit_side = models.CharField(max_length=20, default="Old", verbose_name="Agregat joyi")
    unit_brand = models.CharField(max_length=50, default="Bitzer", verbose_name="Agregat brendi")
    
    # Mahsulot parametrlari (AI uchun)
    product_type = models.CharField(max_length=50, default="Go'sht", verbose_name="Mahsulot turi")
    storage_temp = models.CharField(max_length=20, default="-18°C", verbose_name="Saqlash harorati")
    opening_freq = models.CharField(max_length=20, default="Kam", verbose_name="Ochilish soni")
    region = models.CharField(max_length=20, default="Mo'tadil", verbose_name="Hudud")
    humidity = models.CharField(max_length=20, default="Standart", verbose_name="Namlik talabi")
    
    # AI natijalari (JSON)
    ai_result = models.JSONField(blank=True, null=True, verbose_name="AI tavsiya natijasi")
    
    # Hisob-kitob natijalari (JSON)
    calculations = models.JSONField(blank=True, null=True, verbose_name="Hisob-kitob natijalari")
    
    # Meta
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Loyiha"
        verbose_name_plural = "Loyihalar"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.room_code} - {self.project_name or 'Nomsiz'}"
    
    def get_wall_mm(self):
        return int(self.wall_thickness.replace('mm', '').strip())
    
    def get_ceiling_mm(self):
        return int(self.ceiling_thickness.replace('mm', '').strip())
    
    def get_floor_mm(self):
        if not self.has_floor:
            return 0
        return int(self.floor_thickness.replace('mm', '').strip())
    
    def get_door_dimensions(self):
        door_map = {
            "Bir tabaqali (90x190)": (900, 1900),
            "Surilma (120x200)": (1200, 2000),
            "Muzlatkich eshigi": (960, 2000),
        }
        return door_map.get(self.door_type, (0, 0))
# orders/models.py - Kassa modellari (formsiz)

class CashTransaction(models.Model):
    """Kassa operatsiyalari (Ichki kirim/chiqim)"""
    CURRENCY_CHOICES = [
        ('UZS', "So'm"),
        ('USD', 'Dollar'),
    ]
    TRANSACTION_TYPES = [
        ('INCOME', 'Kirim (Naqd tushum)'),
        ('EXPENSE', 'Chiqim (Sarflash)'),
        ('EXTERNAL_INCOME', 'Tashqi kirim (Click/Payme)'),
        ('EXTERNAL_EXPENSE', 'Tashqi chiqim'),
    ]
    
    CATEGORY_CHOICES = [
        ('SALARY', 'Ish haqi'),
        ('MATERIAL', 'Material xaridi'),
        ('UTILITY', 'Kommunal to\'lovlar'),
        ('TRANSPORT', 'Transport xarajatlari'),
        ('REPAIR', 'Ta\'mirlash'),
        ('OFFICE', 'Kantselyariya'),
        ('TAX', 'Soliq'),
        ('OTHER', 'Boshqa'),
        ('CUSTOMER_PAYMENT', 'Mijoz to\'lovi'),
        ('ORDER_PAYMENT', 'Buyurtma to\'lovi'),
    ]
    
    # ✅ TO'LOV USULLARI - KARTA QO'SHILDI
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Naqd pul'),
        ('CARD', 'Plastik karta'),      # ✅ QO'SHILDI
        ('CLICK', 'Click'),
        ('PAYME', 'Payme'),
        ('BANK', 'Bank'),
    ]
    
    transaction_id = models.CharField(max_length=50, unique=True, verbose_name="Operatsiya ID")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, verbose_name="Operatsiya turi")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name="Kategoriya")
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Summa")
    payment_method = models.CharField(
        max_length=10, 
        choices=PAYMENT_METHOD_CHOICES,  # ✅ YANGILANDI
        default='CASH', 
        verbose_name="To'lov usuli"
    )
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default='UZS',
        verbose_name="Valyuta"
    )
    # Bog'lanishlar
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='cash_transactions', verbose_name="Buyurtma")
    customer_name = models.CharField(max_length=200, blank=True, verbose_name="Mijoz nomi")
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cash_transactions', verbose_name="Amalni bajargan")
    
    # Operatsiya ma'lumotlari
    description = models.TextField(verbose_name="Tavsif")
    receipt_number = models.CharField(max_length=50, blank=True, verbose_name="Chek raqami")
    transaction_date = models.DateTimeField(default=timezone.now, verbose_name="Operatsiya vaqti")
    
    # Qo'shimcha ma'lumotlar
    external_payment_id = models.CharField(max_length=100, blank=True, verbose_name="Tashqi to'lov ID (Click/Payme)")
    external_payment_data = models.JSONField(default=dict, blank=True, verbose_name="Tashqi to'lov ma'lumotlari")
    
    # Holat
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Kutilmoqda'),
        ('COMPLETED', 'Bajarilgan'),
        ('CANCELLED', 'Bekor qilingan'),
    ], default='COMPLETED')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-transaction_date']
        verbose_name = "Kassa operatsiyasi"
        verbose_name_plural = "Kassa operatsiyalari"
    
    def __str__(self):
        return f"{self.transaction_id} - {self.get_transaction_type_display()}: {self.amount} {self.currency}"
    
    def save(self, *args, **kwargs):
        if not self.transaction_id:
            import uuid
            self.transaction_id = f"TR-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

class DailyCashReport(models.Model):
    """Kunlik kassa hisoboti"""
    report_date = models.DateField(unique=True, verbose_name="Hisobot sanasi")
    
    # Kun boshidagi qoldiq
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Kun boshidagi naqd")
    
    # Kirimlar
    cash_income = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Naqd kirim")
    click_income = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Click kirim")
    payme_income = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Payme kirim")
    bank_income = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Bank kirim")
    total_income = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Jami kirim")
    
    # Chiqimlar
    cash_expense = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Naqd chiqim")
    click_expense = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Click chiqim")
    payme_expense = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Payme chiqim")
    bank_expense = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Bank chiqim")
    total_expense = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Jami chiqim")
    
    # Kategoriyalar bo'yicha
    category_breakdown = models.JSONField(default=dict, verbose_name="Kategoriyalar bo'yicha taqsimot")
    
    # Kun oxiridagi qoldiq
    expected_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Kutilgan qoldiq")
    actual_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Haqiqiy qoldiq")
    difference = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Farq")
    
    # Qo'shimcha
    notes = models.TextField(blank=True, verbose_name="Izoh")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_reports', verbose_name="Yaratgan")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Kunlik kassa hisoboti"
        verbose_name_plural = "Kunlik kassa hisobotlari"
    
    def __str__(self):
        return f"Kassa hisoboti - {self.report_date}"
    
    def calculate_totals(self):
        """Jami kirim va chiqimlarni hisoblash"""
        self.total_income = self.cash_income + self.click_income + self.payme_income + self.bank_income
        self.total_expense = self.cash_expense + self.click_expense + self.payme_expense + self.bank_expense
        self.expected_balance = self.opening_balance + self.total_income - self.total_expense
        self.difference = self.actual_balance - self.expected_balance if self.actual_balance else 0
        return self


class CashRegisterBalance(models.Model):
    """Joriy kassa qoldig'i"""
    cash_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Naqd qoldiq (UZS)")
    cash_balance_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Naqd qoldiq (USD)")
    last_updated = models.DateTimeField(auto_now=True, verbose_name="Oxirgi yangilanish")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Yangilagan")
    
    class Meta:
        verbose_name = "Kassa qoldig'i"
        verbose_name_plural = "Kassa qoldiqlari"
    
    def __str__(self):
        return f"Joriy qoldiq: {self.cash_balance} so'm | {self.cash_balance_usd} USD"
