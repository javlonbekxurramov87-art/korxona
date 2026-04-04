from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
import os
from decimal import Decimal 

# Barcha modellar bitta joydan import qilindi
from .models import Order, MaterialTransaction, Material, Category, Worker 

# -----------------------------------
# 1. BUYURTMA (ORDER) FORMALARI
# -----------------------------------

ESHIK_QALINLIK_CHOICES  = [
    ('', '--- Tanlang ---'),
    ('5', '5 sm'),
    ('8', '8 sm'), 
    ('10', '10 sm'),
    ('15', '15 sm'),
]

import os
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from django import forms
from .models import Order, Worker
from django.core.exceptions import ValidationError
from django.utils import timezone

PANEL_THICKNESS_CHOICES = [
    ('', '--- Panel Qalinligini Tanlang ---'),
    ('5', '5 sm'),
    ('8', '8 sm'),
    ('10', '10 sm'),
    ('15', '15 sm'),

]
from django import forms
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Order, Worker

class OrderForm(forms.ModelForm):
    worker_type = forms.ChoiceField(
        choices=[
            ('', '--- Ish Turini Tanlang ---'),
            ('LIST', 'List Ustasi (PIR/PUR Panel)'),
            ('ESHIK', 'Eshik Ustasi'),
            ('LIST_ESHIK', 'List va Eshik Ustasi (Universal)'),
        ],
        required=True,
        label="Ish Turi",
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_worker_type'})
    )


    eshik_qalinligi = forms.TypedChoiceField(
        choices=ESHIK_QALINLIK_CHOICES,
        coerce=lambda x: int(x) if x not in ("", None) else None,
        required=False,
        label="Eshik qalinligi",
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_eshik_qalinligi'})

    )
    class Meta:
        model = Order
        fields = [
            'pdf_file', 'customer_unique_id','customer_name', 'product_name', 
            'worker_type', 'eshik_turi', 'parog_turi', 'eshik_yonalishi', 
            'balandligi', 'eni', 'zamokli_eshik',
            'panel_type', 'panel_subtype', 'panel_thickness', 'panel_kvadrat',
            'total_price', 'prepayment', 'deadline', 'assigned_workers', 
            'comment', 'status', 'needs_manager_approval','eshik_qalinligi', 
        ]
        
        widgets = {
            'assigned_workers': forms.CheckboxSelectMultiple(attrs={'id': 'id_assigned_workers'}),
            'deadline': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'customer_unique_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ID kiriting'}),
            'customer_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Xaridor nomi'}),
            'product_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mahsulot nomi'}),
            'panel_type': forms.Select(attrs={'class': 'form-control', 'id': 'id_panel_type'}),
            'panel_subtype': forms.Select(attrs={'class': 'form-control', 'id': 'id_panel_subtype'}),
            'panel_thickness': forms.Select(attrs={'class': 'form-control', 'id': 'id_panel_thickness'}),
            'panel_kvadrat': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'total_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_total_price'}),
            'prepayment': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_prepayment'}),
            'eshik_turi': forms.Select(attrs={'class': 'form-control', 'id': 'id_eshik_turi'}),
            'parog_turi': forms.Select(attrs={'class': 'form-control'}),
            'eshik_yonalishi': forms.Select(attrs={'class': 'form-control'}),
            'balandligi': forms.NumberInput(attrs={'class': 'form-control'}),
            'eni': forms.NumberInput(attrs={'class': 'form-control'}),
            'zamokli_eshik': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_zamokli_eshik'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'pdf_file': forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'}),
            'comment': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'needs_manager_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Faqat LIST, ESHIK va LIST_ESHIK rolli ustalarni olish
        target_roles = ['LIST', 'ESHIK', 'LIST_ESHIK']
        filtered_workers = Worker.objects.filter(role__in=target_roles)
        
        # Maydonni filtrlangun queryset bilan to'ldirish
        self.fields['assigned_workers'].queryset = filtered_workers
        self.fields['assigned_workers'].label_from_instance = lambda obj: f"{str(obj)} ({obj.role})"
        
        self.fields['status'].initial = 'KIRITILDI'
        
        # Majburiy emas deb belgilash
        optional_fields = ['panel_type', 'panel_thickness', 'eshik_turi', 
                           'panel_subtype', 'status', 'parog_turi', 
                           'eshik_yonalishi', 'balandligi', 'eni', 'prepayment', 'panel_kvadrat', 'customer_unique_id',
        'assigned_workers']
        for field in optional_fields:
            if field in self.fields:
                self.fields[field].required = False

       
        worker_type = self.data.get('worker_type') if self.data else (self.instance.worker_type if self.instance else None)
        all_workers = Worker.objects.all()

        # if worker_type == 'LIST':
        #     self.fields['assigned_workers'].queryset = all_workers.filter(role__in=['LIST', 'LIST_ESHIK'])
        # elif worker_type == 'ESHIK':
        #     self.fields['assigned_workers'].queryset = all_workers.filter(role__in=['ESHIK', 'LIST_ESHIK'])
        # elif worker_type == 'LIST_ESHIK':
        #     self.fields['assigned_workers'].queryset = all_workers.filter(role__in=['LIST', 'ESHIK', 'LIST_ESHIK'])
        # else:
        #     self.fields['assigned_workers'].queryset = all_workers

    def clean_prepayment(self):
        """Zalog kiritilmasa yoki bo'sh bo'lsa, uni 0 deb qaytaradi"""
        prepayment = self.cleaned_data.get('prepayment')
        if prepayment is None:
            return 0
        return prepayment

    def clean(self):
        cleaned_data = super().clean()
        worker_type = cleaned_data.get('worker_type')
        assigned_workers = cleaned_data.get('assigned_workers')
        total_price = cleaned_data.get('total_price') or 0
        prepayment = cleaned_data.get('prepayment') or 0

        # Zalog mantiqi: Agar zalog kiritilmagan bo'lsa, u avtomatik 0 bo'ladi
        # Shunda qarz (Total - Prepayment) avtomatik Total summaning o'ziga teng bo'ladi
        if not assigned_workers:
            self.add_error('assigned_workers', "Kamida bitta usta tanlashingiz shart!")

        # 1. Eshik mantiqi tekshiruvi
        if worker_type in ['ESHIK', 'LIST_ESHIK']:
            required_eshik_fields = [
                'eshik_turi', 'parog_turi', 'eshik_yonalishi',
                'balandligi', 'eni',
            ]
            for field in required_eshik_fields:
                if not cleaned_data.get(field):
                    self.add_error(field, "Ushbu maydon to'ldirilishi shart!")

        

        # 2. Panel mantiqi tekshiruvi
        if worker_type in ['LIST', 'LIST_ESHIK']:
            if not cleaned_data.get('panel_type'):
                self.add_error('panel_type', "Panel turini tanlang!")
            if not cleaned_data.get('panel_thickness'):
                self.add_error('panel_thickness', "Panel qalinligini tanlang!")

        return cleaned_data

class EshikForm(forms.ModelForm):
    """Eshik buyurtmalari uchun maxsus form"""
    
    # Eshik turlari uchun radio button yaratish
    ESHIK_TURI_CHOICES = [
        ('', '--- Tanlang ---'),  # Bo'sh variant qo'shamiz
        ('F1', 'F1'),
        ('F2', 'F2'),
        ('F3', 'F3'),
        ('F4', 'F4'),
        ('F5', 'F5'),
        ('F6', 'F6'),
        ('F7', 'F7'),
        ('F8', 'F8'),
    ]
    
    eshik_turi = forms.ChoiceField(
        choices=ESHIK_TURI_CHOICES,
        required=False,
        label="Eshik Turi",
        widget=forms.Select(attrs={  # Select ni RadioSelect o'rniga ishlatamiz
            'class': 'form-control eshik-select',
            'id': 'id_eshik_turi_select'
        })
    )
    
    zamokli_eshik = forms.BooleanField(
        required=False,
        label="Zamokli Eshik",
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input', 
            'id': 'id_zamokli_eshik'
        })
    )
    
    class Meta:
        model = Order
        fields = ['eshik_turi', 'zamokli_eshik']



class StartImageUploadForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['start_image']
        widgets = {
            'start_image': forms.FileInput(attrs={
                'accept': 'image/*',
                'class': 'form-control',
                'required': False
            })
        }
    
    def clean_start_image(self):
        start_image = self.cleaned_data.get('start_image')
        if not start_image:
            raise ValidationError("Boshlash rasmi majburiy") 
        if start_image.size > 5 * 1024 * 1024:
            raise ValidationError("Rasm hajmi 5MB dan oshmasligi kerak (Maks. 5MB)")
        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        ext = os.path.splitext(start_image.name)[1].lower()
        if ext not in allowed_extensions:
            raise ValidationError("Faqat rasm fayllarini yuklash mumkin (JPG, PNG, GIF, BMP, WebP)")
        return start_image
    
# forms.py faylida EshikForm classini yangilang yoki qo'shing
# forms.py - EshikForm classini yangilang


class FinishImageUploadForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['finish_image']
        widgets = {
            'finish_image': forms.FileInput(attrs={
                'accept': 'image/*',
                'class': 'form-control',
                'required': False
            })
        }
    
    def clean_finish_image(self):
        finish_image = self.cleaned_data.get('finish_image')
        if not finish_image:
            raise ValidationError("Tugatish rasmi majburiy")
        if finish_image.size > 5 * 1024 * 1024:
            raise ValidationError("Rasm hajmi 5MB dan oshmasligi kerak (Maks. 5MB)")
        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        ext = os.path.splitext(finish_image.name)[1].lower()
        if ext not in allowed_extensions:
            raise ValidationError("Faqat rasm fayllarini yuklash mumkin (JPG, PNG, GIF, BMP, WebP)")
        return finish_image

class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['status']
        widgets = {
            'status': forms.Select(attrs={
                'class': 'form-control',
                'onchange': 'this.form.submit()'
            })
        }

# -----------------------------------
# 2. MATERIAL TRANSACTION FORMALARI
# -----------------------------------

# orders/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
import os
from decimal import Decimal 
from .models import Order, MaterialTransaction, Material, Category 

# ============================================
# Material Transaction Form uchun CUSTOM FIELD
# ============================================
from .models import Order, MaterialTransaction, Material, Category
class MaterialChoiceField(forms.ModelChoiceField):
    """Materiallarni to'liq ma'lumot bilan ko'rsatish"""
    
    def label_from_instance(self, obj):
        """Materialni: Nomi (BIRLIK) - Kategoriya - Qoldiq formatida ko'rsatish"""
        category_name = obj.category.name if obj.category else 'Kategoriyasiz'
        return f"{obj.name} ({obj.unit.upper()}) - Kategoriya: {category_name} - Qoldiq: {obj.quantity:.3f}"

from django import forms
from decimal import Decimal
from .models import MaterialTransaction, Material, Category, Order
import json
from django import forms
from decimal import Decimal
from .models import Material, MaterialTransaction, Category

class MaterialTransactionForm(forms.ModelForm):
    # ✅ To'g'ri field e'lon qilish
    transaction_type = forms.ChoiceField(
        choices=MaterialTransaction.TRANSACTION_TYPES,
        label="Harakat turi",
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    
    material = forms.ModelChoiceField(
        queryset=Material.objects.none(),  # Boshlang'ich qiymat
        label="Material *",
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-control select2',
            'id': 'id_material'
        })
    )
    
    quantity_change = forms.DecimalField(
        max_digits=15,
        decimal_places=3,
        label="Miqdor *",
        min_value=0.001,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.001',
            'id': 'id_quantity_change'
        })
    )
    
    received_by = forms.CharField(
        max_length=100,
        required=False,
        label="Kimga/Kimdan",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Masalan: Ali Valiyev yoki 1-sex'
        })
    )
    
    notes = forms.CharField(
        required=False,
        label="Izoh",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Qo\'shimcha ma\'lumotlar...'
        })
    )
    
    # ✅ Yangi maydonlar
    new_category_name = forms.CharField(
        max_length=100,
        required=False,
        label="Yangi Kategoriya Yaratish",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Yangi kategoriya nomi...',
            'id': 'id_new_category_name'
        })
    )
    
    product_name = forms.CharField(
        max_length=255,
        required=False,
        label="Maxsulot nomi",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Maxsulot nomi...',
            'id': 'id_product_name'
        })
    )

    create_batch_barcode = forms.BooleanField(
        required=False, 
        label="Partiya Barcode yaratish",
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'id': 'id_create_batch_barcode'
        })
    )

    class Meta:
        model = MaterialTransaction
        fields = [
            'transaction_type', 
            'material', 
            'quantity_change', 
            'received_by', 
            'notes'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # ✅ To'g'ri queryset o'rnatish
        self.fields['material'].queryset = Material.objects.all().select_related('category').order_by('name')
        
        # ✅ Kategoriya fieldini to'g'ri e'lon qilish
        self.fields['category'] = forms.ModelChoiceField(
            queryset=Category.objects.all().order_by('name'),
            required=False,
            label="Mavjud Kategoriya",
            widget=forms.Select(attrs={
                'class': 'form-control',
                'id': 'id_category'
            })
        )
        
        # ✅ Order fieldi agar mavjud bo'lsa
        if hasattr(self, 'Order') or 'Order' in globals():
            try:
                from .models import Order
                self.fields['order'] = forms.ModelChoiceField(
                    queryset=Order.objects.all(),
                    required=False,
                    label="Buyurtma",
                    widget=forms.Select(attrs={'class': 'form-control'})
                )
            except:
                self.fields['order'] = forms.CharField(
                    max_length=100,
                    required=False,
                    label="Buyurtma raqami",
                    widget=forms.TextInput(attrs={'class': 'form-control'})
                )
        else:
            self.fields['order'] = forms.CharField(
                max_length=100,
                required=False,
                label="Buyurtma raqami",
                widget=forms.TextInput(attrs={'class': 'form-control'})
            )

    def clean(self):
        cleaned_data = super().clean()
        transaction_type = cleaned_data.get('transaction_type')
        material = cleaned_data.get('material')
        quantity = cleaned_data.get('quantity_change')
        
        print(f"DEBUG - Material: {material}")  # Debug uchun
        
        # 1. Material MAJBURIY tekshiruvi
        if not material:
            raise forms.ValidationError({
                'material': "Materialni tanlash majburiy!"
            })
        
        # 2. Kategoriya validatsiyasi
        new_cat = cleaned_data.get('new_category_name')
        old_cat = cleaned_data.get('category')
        
        if new_cat and old_cat:
            raise forms.ValidationError({
                'new_category_name': "Ham yangi, ham mavjud kategoriyani tanlab bo'lmaydi.",
                'category': "Iltimos, faqat bittasini tanlang."
            })
        
        # 3. Chiqim uchun qoldiq tekshiruvi
        if transaction_type == 'OUT' and quantity and material:
            try:
                # Materialni bazadan yangilash
                material.refresh_from_db()
                
                if Decimal(str(quantity)) > Decimal(str(material.quantity)):
                    raise forms.ValidationError({
                        'quantity_change': (
                            f"❌ Omborda yetarli qoldiq yo'q! "
                            f"Mavjud: {material.quantity:.3f} {material.unit}, "
                            f"So'ralgan: {quantity:.3f}"
                        )
                    })
            except Exception as e:
                raise forms.ValidationError(f"Qoldiqni tekshirishda xatolik: {str(e)}")
        
        # 4. Yangi kategoriya yaratish
        if new_cat:
            # Kategoriya mavjudligini tekshirish
            if Category.objects.filter(name__iexact=new_cat.strip()).exists():
                raise forms.ValidationError({
                    'new_category_name': "Bu kategoriya allaqachon mavjud!"
                })
        
        return cleaned_data
    
    def save(self, commit=True):
        """Formani saqlash - materialni to'g'ri bog'lash"""
        instance = super().save(commit=False)
        
        # ✅ Yangi kategoriya yaratish
        new_cat_name = self.cleaned_data.get('new_category_name')
        if new_cat_name:
            category, created = Category.objects.get_or_create(
                name=new_cat_name.strip(),
                defaults={'description': f"Avtomatik yaratilgan: {new_cat_name}"}
            )
            # Material kategoriyasini o'zgartirish
            if instance.material:
                instance.material.category = category
                instance.material.save()
        
        # ✅ Maxsulot nomini saqlash
        product_name = self.cleaned_data.get('product_name')
        if product_name and instance.material:
            # Maxsulot nomini notes ga qo'shish
            if instance.notes:
                instance.notes = f"Maxsulot: {product_name}\n{instance.notes}"
            else:
                instance.notes = f"Maxsulot: {product_name}"
        
        if commit:
            instance.save()
            self.save_m2m()  # Agar many-to-many maydonlari bo'lsa
        
        return instance

# forms.py - MaterialChoiceField va MaterialForm ni yangilash
from .models import Material

class MaterialChoiceField(forms.ModelChoiceField):
    """Materiallarni to'liq ma'lumot bilan ko'rsatish"""
    
    def label_from_instance(self, obj):
        """Materialni: Nomi → Maxsulot (BIRLIK) - Kategoriya - Qoldiq formatida ko'rsatish"""
        category_name = obj.category.name if obj.category else 'Kategoriyasiz'
        
        # 🔴 Maxsulot nomini ham qo'shamiz
        if obj.product_name:
            display_text = f"{obj.name} → {obj.product_name} ({obj.unit.upper()})"
        else:
            display_text = f"{obj.name} ({obj.unit.upper()})"
            
        return f"{display_text} - Kategoriya: {category_name} - Qoldiq: {obj.quantity:.3f}"


# 🔴 YANGI: Materialni yaratish/tahrirlash formasi
class MaterialForm(forms.ModelForm):
    """Material yaratish va tahrirlash uchun forma."""
    
    class Meta:
        model = Material
        fields = [
            'name', 'product_name', 'category', 'unit',
            'quantity', 'price_per_unit', 'min_stock_level'
        ]
        
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Material nomi...',
                'required': True
            }),
            'product_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Maxsulot nomi (ixtiyoriy)...'
            }),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'unit': forms.Select(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.001',
                'min': '0'
            }),
            'price_per_unit': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0'
            }),
            'min_stock_level': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.001',
                'min': '0'
            }),
        }
    
    def clean_name(self):
        name = self.cleaned_data.get('name')
        if not name:
            raise ValidationError("Material nomi majburiy")
        
        qs = Material.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Bu material nomi allaqachon mavjud")
        return name

# -----------------------------------
# 3. FILTRLASH FORMALARI
# -----------------------------------

class OrderFilterForm(forms.Form):
    """Buyurtmalarni filtrlash uchun forma"""
    
    STATUS_CHOICES = [('', 'Barcha holatlar')] + list(Order.STATUS_CHOICES)
    
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    customer_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Xaridor nomi boʻyicha qidirish...'
        })
    )
    
    order_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Buyurtma raqami boʻyicha qidirish...'
        })
    )
    
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    # 🔴 YANGI: Ish turi uchun filtr
    worker_type = forms.ChoiceField(
        choices=[
            ('', 'Barcha ish turlari'),
            ('LIST', 'List Ustasi uchun'),
            ('ESHIK', 'Eshik Ustasi uchun'),
        ],
        required=False,
        label="Ish Turi",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
