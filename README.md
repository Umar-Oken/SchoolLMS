# School LMS backend

## Lokal ishga tushirish

PostgreSQL’ni operatsion tizimingizda o‘rnating va ishga tushiring yoki mavjud
PostgreSQL serveridan foydalaning. PostgreSQL o‘zining ma’lumotlar katalogida
ma’lumotni saqlaydi; saqlash va backup siyosati shu server tomonidan boshqariladi.
Django migratsiyalari sxemani yangilaydi, mavjud qatorlarni o‘chirib tashlamaydi.

1. Virtual environment yarating va faollashtiring:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Runtime paketlarini o‘rnating, `.env.example`dan `.env` nusxa yarating va
   PostgreSQL ulanish parametrlarini o‘zingizning muhitga moslang. `.env`ga
   haqiqiy secret va parollarni kiriting, uni repositoryga qo‘shmang.

   ```sh
   pip install -r requirements.txt
   cp .env.example .env
   ```

   Lokal PostgreSQL uchun odatda `DB_HOST=127.0.0.1` va `DB_PORT=5432`
   ishlatiladi. Masofaviy server uchun `DB_HOST`, `DB_PORT`, `DB_NAME`,
   `DB_USER` va `DB_PASSWORD`ni tegishli qiymatlarga sozlang.
3. Django sozlamalari va DB ulanishini tekshirib, migratsiyalarni qo‘llang:

   ```sh
   python manage.py check
   python manage.py check --database default
   python manage.py migrate
   python manage.py showmigrations accounts
   python manage.py runserver
   ```

Production’da `DEBUG=false`, kuchli `DJANGO_SECRET_KEY`, aniq
`DJANGO_ALLOWED_HOSTS` va PostgreSQL ulanish qiymatlarini muhit o‘zgaruvchilari
orqali bering. P081’da Gunicorn production dependency sifatida o‘rnatilib,
`gunicorn root.wsgi:application` komandasi sozlangach, uni systemd, Supervisor
yoki hosting process manager orqali boshqaring. Development serverini
production’da ishlatmang.

P055’da Redis va Celery integratsiyasi sozlangandan keyin Redis’ni lokal servis
sifatida yoki mavjud server orqali ishga tushiring; ulanish manzilini
`REDIS_URL` environment variable orqali bering. Worker va Beat alohida
operatsion tizim processlari bo‘lib, har biri uchun quyidagi komandani alohida
ishga tushiring:

```sh
celery -A root worker -l info
celery -A root beat -l info
```

Beat’ning faqat bitta nusxasini ishga tushiring. Worker va Beat’ni systemd,
Supervisor yoki hosting process manager orqali boshqarish mumkin.

## P006: testlar va OpenAPI

Test va schema vositalarini development muhitiga o‘rnating:

```sh
pip install -r requirements-dev.txt
pytest apps/tests.py accounts/tests.py
python manage.py spectacular --validate --file /tmp/schoollms-openapi.yaml
```

Testlar sozlangan PostgreSQL bazasidan foydalanadi.

## Sintetik development ma’lumotlari

`seed_school` faqat `DJANGO_ENV=development` va `DJANGO_DEBUG=true` yoki
`DJANGO_ENV=test` muhitida ishlaydi. U bitta 2026–2027 o‘quv yili, to‘rt chorak,
7-A va 7-B sinflari, olti sintetik foydalanuvchi, uch fan, kurslar hamda
biriktirishlarni yaratadi. Barcha pupil va teacher ma’lumotlari o‘ylab topilgan;
ular haqiqiy shaxslarni anglatmaydi va haqiqiy pupil importi emas.

Yangi seed hisoblari uchun parollarni `.env`ga yoki process environment’ga
`SEED_TEACHER_ONE_PASSWORD`, `SEED_TEACHER_TWO_PASSWORD`,
`SEED_PUPIL_ONE_PASSWORD`, `SEED_PUPIL_TWO_PASSWORD`,
`SEED_PUPIL_THREE_PASSWORD` va `SEED_PUPIL_FOUR_PASSWORD` nomlari bilan kiriting.
Qiymatlarni kodga, logga yoki repository’ga yozmang. Mavjud seed hisoblarining
parollari qayta ishga tushirishda o‘zgartirilmaydi.

```sh
python manage.py seed_school
```

Komanda transaction ichida ishlaydi va qayta ishga tushirilganda takroriy
asosiy yozuvlar yaratmaydi. U foydalanuvchilarning parollarini Django’ning
`create_user()` manageri orqali hash qilib saqlaydi.

## Kurslarni o‘qish API’si

- `GET /api/v1/courses/` — foydalanuvchiga tegishli kurslar ro‘yxati.
- `GET /api/v1/courses/{id}/` — shu ro‘yxatdagi bitta kurs.

Ikkala endpoint ham autentifikatsiyani talab qiladi. Pupil faqat faol
enrollment’li kurslarini, teacher esa faol teaching assignment’i bor kurslarini
ko‘radi. Javob fan kodi/nomi, sinf darajasi/bo‘limi, teacher display nomi va
o‘quv yili yorlig‘ini o‘z ichiga oladi; user profili yoki credential maydonlari
chiqarilmaydi. Muvaffaqiyatli javob `200`; autentifikatsiyasiz so‘rov `403`;
scope’dan tashqari detail ID `404` qaytaradi, scope’ga mos bo‘lmagan list bo‘sh
massiv qaytaradi. Misol javob:

```json
[
  {
    "id": 1,
    "subject": {"code": "SYN-MATH", "title": "Matematika"},
    "school_class": {"grade_level": 7, "section": "A"},
    "teacher_display": "Sami O‘qituvchi",
    "year": {"label": "2026-2027"}
  }
]
```

Auth API hozircha yo‘q; testlar DRF `force_authenticate`dan foydalanadi. Lokal
brauzer misoli: `createsuperuser` interaktiv komandasi bilan `teacher` roli
berilgan superuser yarating; `manage.py shell`da uni `assign_teacher` service
orqali kursga biriktiring; so‘ng `/admin/`ga kirgan ayni brauzer sessiyasida
`http://127.0.0.1:8000/api/v1/courses/`ni oching. Superuser bo‘lish kurs
scope’ini chetlab o‘tmaydi.

`/api/schema/`, `/api/docs/` va `/api/redoc/` hujjat endpointlari faqat
`DJANGO_DEBUG=true` bo‘lganda mavjud.
ReDoc JavaScript fayli `drf-spectacular-sidecar` orqali lokal xizmat qilinadi;
CDN yoki internetga ulanish talab qilinmaydi.
