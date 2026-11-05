# School LMS: boshlovchilar uchun qo‘llanma

Ushbu qo‘llanma lokal development server va Swagger UI uchun. Mavjud yoki
production ma’lumotlar bazasini reset qilmang. Buyruqlarni repository root
papkasida bajaring.

## 1. Loyihani tayyorlash va ishga tushirish

1. PostgreSQL serverini ishga tushiring va `.env.example`dan `.env` yarating.
   `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`ni o‘z muhitga
   moslang. `DJANGO_SECRET_KEY`ni xavfsiz, maxfiy qiymat bilan to‘ldiring.
   `.env` Git’ga qo‘shilmasligi kerak.
2. Virtual environment va paketlar:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Database sozlamasi va schema holatini tekshiring, so‘ng migratsiyalarni
   qo‘llang. `migrate` qatorlarni seed/reset qilmaydi; production’da oldin
   migration rejasini va backup siyosatini tekshiring.

   ```sh
   python manage.py check
   python manage.py showmigrations
   python manage.py migrate
   python manage.py runserver
   ```

## 2. Swagger’ni ochish

Development server ishlab turganda `http://127.0.0.1:8000/api/docs/`ni oching.
Schema `http://127.0.0.1:8000/api/schema/`, ReDoc
`http://127.0.0.1:8000/api/redoc/`da. Bu uchalasi faqat
`DJANGO_DEBUG=true` bo‘lganda mavjud.

## 3. Birinchi superuser/operatorni yaratish

Yangi development database uchun birinchi texnik boshqaruvchi terminalda
interaktiv yaratiladi:

```sh
python manage.py createsuperuser
```

Username va parolni promptlarda kiriting; parol ekranga chiqarilmaydi. Custom
`role` so‘ralganda mavjud `teacher` qiymatini tanlang. `pupil` va `teacher`
— loyihadagi yagona business role’lar; `admin` degan role yo‘q. Bu Django
superuser bo‘lgani uchun account/akademik operator permission’larini
cheklovsiz oladi. Superuser loginini kundalik teacher hisobi sifatida
tarqatmang. Tizim default parol yaratmaydi.

Mavjud database’da superuser yo‘qligini avval database administratori bilan
tasdiqlang; mavjud user’larni takror yaratmang. Bir xil username mavjud bo‘lsa
`createsuperuser` ishlamaydi. Parolni hech qachon command argumentiga yozmang.

## Django Admin’da user va permissionlar

`/admin/`dagi **Accounts → Users** bo‘limi custom `User` modeli, role,
faollik, staff va superuser statuslarini ko‘rsatadi. **Authentication and
Authorization → Groups** bo‘limida guruh va guruh permissionlari boshqariladi.
Bevosita user permissionlari va group permissionlarini faqat superuser
tahrirlaydi; oddiy staff foydalanuvchisi bu privilege’larni o‘zgartira olmaydi.
Superuser admin parol formasidan foydalanib user parolini boshqarishi mumkin;
user yaratish/saqlash Django’ning password hashing formalaridan foydalanadi.

Oddiy operatorga `manage_accounts` yoki `manage_academics` berish kerak bo‘lsa,
superuser tomonidan tasdiqlangan `bootstrap_teacher` command’idan foydalaning.
U mavjud faol teacherga bitta capability beradi va `CapabilityGrantAudit`ga
actor, target va sababni yozadi; mavjud user yoki parolni o‘zgartirmaydi.

## 4. Account yaratish va vakolatli teacher tayyorlash

`POST /api/v1/accounts/` uchun avval JWT access token olgan
`manage_accounts` operatori kerak. Swagger’dagi **Authorize** tugmasidan
Bearer access token kiriting; CSRF token talab qilinmaydi. Body:

```json
{
  "username": "teacher02",
  "first_name": "Ali",
  "last_name": "Valiyev",
  "role": "teacher",
  "password": "<kuchli-parol>",
  "preferred_language": "uz"
}
```

`username`, `first_name`, `last_name`, `role` va `password` majburiy;
`preferred_language` ixtiyoriy. Parol Django hasher bilan saqlanadi va
`201` javobda hech qachon qaytarilmaydi. Yangi account darhol parol almashtirish
talab qilmaydi.

Oddiy teacher/pupil endpoint orqali boshqa account yarata olmaydi yoki
capability bera olmaydi. Vakolatlarni faqat superuser beradi. Kundalik
teacherga alohida capability kerak bo‘lsa, mavjud faol teacherga quyidagicha
bitta permission beriladi:

```sh
python manage.py bootstrap_teacher \
  --actor <superuser-username> \
  --target <existing-teacher-username> \
  --capability manage_accounts \
  --reason "Maktab rahbari tasdiqladi"
```

`--capability` uchun `manage_accounts` yoki `manage_academics` qiymatini
tanlang. `manage_accounts` account yaratish/reset qilishga, `manage_academics`
yil/sinf/fan/course katalogini boshqarishga ruxsat beradi. Ikkalasi mustaqil
va faqat superuser CLI orqali beriladi; account API ro‘l/permission ko‘tarmaydi.

## 5. JWT login va Swagger Authorize

API JWT Bearer autentifikatsiyasidan foydalanadi. Login, logout va account
create uchun CSRF token kerak emas. Django admin/session uchun CSRF middleware
saqlanadi; bu API’da SessionAuthentication ishlatilmaydi.

1. `POST /api/v1/auth/login/`ni ochib username va parolni yuboring:

   ```json
   {"username": "teacher02", "password": "<vaqtinchalik-parol>"}
   ```

2. `200` javobdagi `access` va `refresh` tokenlarni oling. Swagger’da
   **Authorize** oynasiga access tokenni kiriting (UI `Bearer` prefiksini boshqaradi);
   himoyalangan so‘rovlarda CSRF header talab qilinmaydi.
3. Access token muddati tugasa `POST /api/v1/auth/token/refresh/`ga
   `{"refresh":"<refresh-token>"}` yuboring. Logout uchun `POST
   /api/v1/auth/logout/`ni Bearer access token va shu refresh token bilan chaqiring;
   logout refresh tokenni blacklist qiladi. Logout body parol so‘ramaydi.

Noto‘g‘ri username/parol yoki JWT `401`, permission rad etilganda `403`,
noto‘g‘ri body `400` qaytaradi. Swagger Authorize oynasida HTTP Basic credential
so‘ralmasligi kerak.

## 6. Account vaqtinchalik parolini almashtirish

Yangi account egasi account create vaqtida o‘rnatilgan parol bilan login qiladi.
Parolni almashtirish kerak bo‘lsa, `POST /api/v1/me/change-password/`ga
quyidagi JSON’ni Bearer access token bilan yuboradi:

```json
{
  "current_password": "<vaqtinchalik-parol>",
  "new_password": "<kuchli-yangi-parol>"
}
```

`200` javobdan keyin protected endpointlar ochiladi. Yangi parol Django
validatorlari bilan tekshiriladi; ayni/zaif parol yoki noto‘g‘ri joriy parol
`400` qaytaradi. Operator boshqa account parolini `POST
/api/v1/accounts/{id}/reset-password/` orqali reset qiladi; request body
bo‘sh bo‘lishi kerak, qaytgan vaqtinchalik parolni xavfsiz yetkazing.

## 7. Profilni ko‘rish va o‘zgartirish

- `GET /api/v1/me/` — joriy account ma’lumotlari.
- `PATCH /api/v1/me/` — faqat `first_name`, `last_name`,
  `preferred_language`ni o‘zgartiradi. Misol:

  ```json
  {"first_name": "Ali", "preferred_language": "uz"}
  ```

- `POST /api/v1/me/change-password/` — avvalgi va yangi parol bilan.

Boshqa foydalanuvchining profili API orqali tahrir qilinmaydi. `username`,
`role`, privilege, enrollment kabi maydonlar profile PATCH’da rad etiladi.

## 8. Boshqa API’lardan foydalanish

API’lar `/api/v1/` prefixidan foydalanadi. Odatda Swagger’da tegishli tagni
oching, `Try it out`ni bosing, path ID/body/headerlarni to‘ldirib `Execute`
qiling. To‘liq metod, ruxsat, request/response namunalari va error’lar
`docs/API_GUIDE.md`da berilgan. Mavjud guruhlar:

- Auth, current profile va operator account lifecycle.
- O‘z scope’idagi course list/detail.
- AcademicYear/Term, SchoolClass, Subject va CourseOffering kataloglari;
  `manage_academics` operatori pupilni sinfga biriktirishi/ko‘chirishi.
- Kurs mavzularini o‘qish, draft yaratish/tahrirlash, archive va publish.

Automatic year promotion, Subject CRUD generatori, grades va tasks API
endpointlari yo‘q. Yopiq term’da yangi topic yaratish, draft’ni
tahrirlash va publish qilish bloklangan. Yangi grade/task write service
qo‘shilganda u ham shared `ensure_term_open()` guard’dan foydalanishi kerak.

## 9. Rol va imkoniyatlar

| Rol/capability | Nima qila oladi |
|---|---|
| `pupil` | O‘z profilini boshqaradi, o‘z enrollment scope’idagi kurslarni va published topic’larni o‘qiydi; kataloglarni ko‘radi |
| Oddiy `teacher` | O‘z profilini boshqaradi; biriktirilgan kurslarni va mavzularni ko‘radi; faol biriktirilgan kursda topic write qiladi |
| `teacher` + `manage_accounts` | Yuqoridagilar va pupil/teacher account yaratish/parolini reset qilish |
| `teacher` + `manage_academics` | Yuqoridagilar va academic cataloglarni boshqarish |
| Django superuser | Texnik admin; barcha Django ruxsatlari bor, ammo business `role` hanuz `teacher` yoki `pupil` bo‘ladi |

`manage_accounts`/`manage_academics` role emas, alohida permissions. Account
API `admin` role yaratmaydi. Kurs o‘qish ham assignment/enrollment scope’iga
bog‘liq; superuser texnik privilege olsa ham business course list scope’ini
avtomatik chetlab o‘tmaydi.

## 10. Logout

`POST /api/v1/auth/logout/`ga Bearer access token va
`{"refresh":"<refresh-token>"}` body yuboring. Javob `204`; refresh token
blacklist qilinadi va undan keyingi refresh so‘rovi `401` qaytaradi. Keyin
qayta login qilish mumkin.

## Xavfsiz foydalanish eslatmalari

- Hech qanday default admin parol yo‘q; parollarni maxfiy va alohida kanalda
  yetkazing. API’da parollar Django hash bilan saqlanadi.
- Production’da `DJANGO_DEBUG=false`, kuchli `DJANGO_SECRET_KEY`, to‘g‘ri
  `DJANGO_ALLOWED_HOSTS` va xavfsiz PostgreSQL credential ishlating. Swagger
  production’da yoqilmaydi.
- `seed_school` faqat `DJANGO_ENV=development` va `DJANGO_DEBUG=true` yoki
  `DJANGO_ENV=test` muhitida, environment’dan berilgan synthetic passwordlar
  bilan ishlaydi. Uni production’da ishga tushirmang; u birinchi admin yaratish
  yo‘li emas.
- Database reset, truncate yoki migration rollback qilmang. Muammoli migration
  bo‘lsa, avval plan/backup va database administrator bilan tekshiring.
