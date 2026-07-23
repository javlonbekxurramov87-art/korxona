#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
import json
import hashlib
import uuid
from datetime import datetime, timedelta


# =========================
# LICENSE CONFIGURATION
# =========================

LICENSE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".license"
)

TRIAL_DAYS = 10

# Sizning maxfiy license key'ingiz
VALID_LICENSE_KEY = "ECO-PROM-2026-PRO-LICENSE"


def get_machine_id():
    """
    Kompyuterga bog'langan unique ID.
    """
    machine_info = (
        str(uuid.getnode()) +
        os.environ.get("COMPUTERNAME", "") +
        os.environ.get("USERNAME", "")
    )

    return hashlib.sha256(
        machine_info.encode()
    ).hexdigest()


def activate_license():
    """
    License key so'raydi.
    """

    print("\n" + "=" * 60)
    print("ECO PROM SYSTEM ACTIVATION")
    print("=" * 60)

    license_key = input("License Key kiriting: ").strip()

    if license_key != VALID_LICENSE_KEY:
        print("\n❌ Noto'g'ri License Key!")
        print("Dastur ishga tushmadi.")
        sys.exit(1)

    data = {
        "activated": True,
        "machine_id": get_machine_id(),
        "activated_at": datetime.now().isoformat(),
        "expires_at": (
            datetime.now() + timedelta(days=365)
        ).isoformat()
    }

    with open(LICENSE_FILE, "w") as f:
        json.dump(data, f, indent=4)

    print("\n✅ License muvaffaqiyatli aktivatsiya qilindi!")
    print("Dasturni qayta ishga tushiring.\n")


def check_license():
    """
    10 kunlik Trial va License tekshiruvi.
    """

    # Agar license mavjud bo'lsa
    if os.path.exists(LICENSE_FILE):

        try:
            with open(LICENSE_FILE, "r") as f:
                license_data = json.load(f)

            # Boshqa kompyuterga ko'chirilgan bo'lsa
            if license_data.get("machine_id") != get_machine_id():
                print("\n❌ Bu License boshqa kompyuterga tegishli!")
                sys.exit(1)

            # License tugagan bo'lsa
            expires_at = datetime.fromisoformat(
                license_data["expires_at"]
            )

            if datetime.now() > expires_at:
                print("\n❌ LICENSE MUDDATI TUGAGAN!")
                activate_license()
                sys.exit(1)

            return True

        except Exception:
            print("\n❌ License fayli buzilgan!")
            activate_license()
            sys.exit(1)

    # =========================
    # 10 KUNLIK TRIAL
    # =========================

    trial_data = {
        "first_run": datetime.now().isoformat(),
        "machine_id": get_machine_id()
    }

    # Birinchi ishga tushirish
    trial_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".trial"
    )

    if not os.path.exists(trial_file):

        with open(trial_file, "w") as f:
            json.dump(trial_data, f, indent=4)

        print("\n✅ 10 kunlik Trial boshlandi.")
        return True

    # Trial ma'lumotini o'qish
    try:

        with open(trial_file, "r") as f:
            data = json.load(f)

        # Kompyuter o'zgargan bo'lsa
        if data.get("machine_id") != get_machine_id():
            print("\n❌ Trial boshqa kompyuterga tegishli!")
            activate_license()
            sys.exit(1)

        first_run = datetime.fromisoformat(
            data["first_run"]
        )

        expire_date = first_run + timedelta(days=TRIAL_DAYS)

        remaining_days = (
            expire_date - datetime.now()
        ).days

        # Trial tugagan
        if datetime.now() >= expire_date:

            print("\n" + "=" * 60)
            print("❌ 10 KUNLIK TRIAL MUDDATI TUGADI")
            print("=" * 60)

            print(
                "\nDasturni davom ettirish uchun License Key kerak."
            )

            activate_license()

            sys.exit(1)

        print(
            f"\n⏳ Trial: {remaining_days} kun qoldi"
        )

        return True

    except Exception:

        print("\n❌ Trial ma'lumotlari buzilgan!")
        activate_license()
        sys.exit(1)


def main():
    """Run administrative tasks."""

    # License tekshirish
    check_license()

    os.environ.setdefault(
        'DJANGO_SETTINGS_MODULE',
        'eco_prom.settings'
    )

    try:
        from django.core.management import (
            execute_from_command_line
        )

    except ImportError as exc:

        raise ImportError(
            "Couldn't import Django. Are you sure it's installed "
            "and available on your PYTHONPATH environment variable? "
            "Did you forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
