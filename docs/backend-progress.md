# Backend prompt progress

Har prompt faqat o‘z qabul mezonlari tekshirilgach `verified` deb belgilanadi.
Hujjat-only promptlarda kod testi sun’iy ravishda talab qilinmaydi. Bajarilmagan
tekshiruv `implemented_unverified` holatida qoladi; dependency yoki muhit to‘siqlari
`blocked` holatida sabab bilan qayd etiladi.

## Status qiymatlari

- `pending` — hali boshlanmagan.
- `in_progress` — ayni paytda bajarilmoqda.
- `implemented_unverified` — ish bajarilgan, qabul mezonlari to‘liq tekshirilmagan.
- `verified` — promptning qabul mezonlari dalil bilan tekshirilgan.
- `blocked` — blocker sabab davom ettirib bo‘lmaydi; sabab va zarur yechimni yozish.

## Promptlar

| Prompt | Holat | Dalil / blocker |
|---|---|---|
| P001 | verified | `docs/backend-scope.md` va progress shabloni yaratildi; mavjud repository va dependency holati qayd etildi. |
| P002 | verified | Django config moslashtirildi; `.env.example`, ignore qoidalari va to‘liq pinned `requirements.txt` tayyorlandi. `manage.py check`, OpenAPI schema validation, production env guard tekshiruvi va pip resolver dry-run o‘tdi. Migration bajarilmadi. |
| P003 | verified | `accounts.User`, role CHECK constraint va `0001_initial.py` yaratildi. 4 ta test (jumladan PostgreSQL ajratilgan test DB’da constraint testi), `manage.py check` va migration drift check o‘tdi. Asosiy DB’ga migrate qilinmadi. |
| P004 | verified | PostgreSQL `DB_PORT=5433` orqali ulandi (hostdagi 5432 band); `check --database default`, barcha migrationlar va `accounts.0001_initial` muvaffaqiyatli. Sintetik User qatori restartdan keyin saqlangani tekshirildi, sinov qatori o‘chirildi. PostgreSQL ulanishi `.env` orqali lokal yoki mavjud DB serveriga sozlanadi; ma’lumotlarni PostgreSQLning o‘zi saqlaydi. Mavjud `psycopg2-binary` qayta o‘rnatilmadi. |
| P006 | verified | `requirements-dev.txt`da pytest/pytest-django va pinned dev dependencies ajratildi; `pytest.ini` qo‘shildi. `pytest apps/tests.py accounts/tests.py` — 10 passed; `manage.py spectacular --validate` schema validation’dan o‘tdi. Production settings’da schema/docs URL’lari yo‘qligi tekshirildi. |
| P007 | verified | `academics` app, AcademicYear/Term va initial migration qo‘shildi. PostgreSQL test DB’da teskari sanalar va takroriy `(year, order)` constraint tomonidan rad etildi; yil chegarasidan tashqaridagi term `full_clean()` validation’da rad etildi. `pytest` — 11 passed, `manage.py check` va `makemigrations --check --dry-run academics` muvaffaqiyatli. |
| P008 | verified | `SchoolClass`, `Subject`, `CourseOffering` va `0002` migration qo‘shildi. PostgreSQL’da to‘liq `pytest` — 20 passed; sinf darajasi, sinf takrorlanishi, offering takrorlanishi, bitta fanning ikki sinfda ishlatilishi va `PROTECT` testlandi. `makemigrations --check --dry-run academics` va `manage.py check` muvaffaqiyatli. |
| P009 | verified | `TeachingAssignment`, sana CHECK/model tekshiruvi va biriktirish/almashtirish xizmatlari qo‘shildi. PostgreSQL’da `pytest -q` — 26 passed; pupil, faol bo‘lmagan teacher, arxivlangan course, sana tartibi va eski assignment tarixining saqlanishi tekshirildi. `makemigrations --check --dry-run academics` va `manage.py check` muvaffaqiyatli. |
| P010 | verified | `ClassMembership`, `Enrollment`, faol sinf/kurs unikal cheklovlari va atomik sinfga biriktirish/almashtirish xizmati qo‘shildi. PostgreSQL’da `pytest -q` — 36 passed; teacher rolini rad etish, ikki faol sinf va kurs yozuvi cheklovlari, sinf almashganda tarix saqlanishi, atomik bekor qilish va sanalar ustma-ust tushganda rad etilishi tekshirildi. `makemigrations --check --dry-run academics` va `manage.py check` muvaffaqiyatli. |
| P011 | verified | `seed_school` management command, environment gate, env orqali parollar, atomik/idempotent synthetic yozuvlar qo‘shildi. Ikki marta ishga tushirishda 1 yil, 4 chorak, 2 sinf, 6 user, 3 fan, 6 kurs, 6 biriktirish, 4 membership va 12 enrollment o‘zgarmasligi; parol hash; production rad etilishi testlandi. `pytest -q` — 38 passed; `manage.py check` va `makemigrations --check --dry-run` o‘tdi. |
| P012 | verified | Explicit `CourseReadSerializer`, `select_related`/prefetch query va IsAuthenticated’li list/detail API qo‘shildi. Pupil faqat faol enrollment’li, teacher faqat faol assignment’li, arxivlanmagan kurslarni ko‘radi; detail scope tashqarisida 404. `force_authenticate` bilan privacy/scope/anonymous testlari qo‘shildi. `pytest -q` — 42 passed; `manage.py check`, `spectacular --validate`, migration drift va `git diff --check` o‘tdi. |
| P013 | verified | `/api/v1/auth/csrf/` CSRF cookie/token va `/api/v1/auth/login/` CSRF-protected session login qo‘shildi. Login serializer, generic credential xatosi, Django `authenticate()`/`login()` session rotate va explicit OpenAPI serializer’lari bor. Enforced-CSRF testlari CSRFsiz rad, token bilan login, session key aylanishi, xato credential’larning bir xil javobi va logging sirlarini tekshirdi. `pytest -q` — 47 passed; `manage.py check`, schema validation, migration drift va `git diff --check` o‘tdi. |
| P014 | verified | SessionAuthentication’li `/api/v1/me/` GET/PATCH va CSRF-himoyalangan `POST /api/v1/auth/logout/` qo‘shildi. Profil faqat ism/familiya/tilni yozadi; username, role, staff va noma’lum maydonlar aniq rad etiladi; PUT yopiq. Testlar anonim ruxsat, taqiqlangan PATCH maydonlari, PUT rad etilishi, logout CSRF va session bekor qilingach kurs endpointining yopilishini tekshirdi. `pytest -q` — 55 passed; `manage.py check`, schema validation, migration drift va `git diff --check` o‘tdi. |
| P015 | verified | CSRF/session bilan himoyalangan `/api/v1/me/change-password/` yangi parolni Django validatorlari bilan tekshiradi, `set_password()` orqali saqlaydi va `must_change_password`ni o‘chiradi. Global IsAuthenticated/SessionAuthentication hamda must-change gate business API’larni yopadi; `/me/`, logout va parol almashtirish istisno. `update_session_auth_hash()` joriy sessiyani yangilab saqlaydi, boshqa sessiyalarni eski auth hash orqali bekor qiladi. Noto‘g‘ri joriy parol, zaif/ayni parol, CSRF, must-change gate, session rotation va boshqa sessiya bekor bo‘lishi testlandi. `pytest -q` — 60 passed; `manage.py check`, schema validation, migration drift va `git diff --check` o‘tdi. |
| P016 | verified | Kurs list/detail scope’i `pupil_course_scope` va `teacher_course_scope` selectorlariga jamlandi; object permission list filteringni to‘ldiradi. Pupil/teacher tarixiy kurs aloqasini o‘qiydi, lekin yozish uchun faol enrollment yoki faol `can_grade=True` assignment talab qilinadi. 2 pupil/2 teacher ID almashtirishlari, tarixiy read va yopiq enrollment/assignment hamda grading huquqi matritsasi testlandi. `pytest -q` — 66 passed; `manage.py check`, schema validation, migration drift va `git diff --check` o‘tdi. |
| P017 | verified | User modeliga `manage_accounts`/`manage_academics` custom Django permissions va `CapabilityGrantAudit` qo‘shildi. Faol superuser actor CLI orqali mavjud faol teacherga bitta capabilityni sabab bilan beradi; pupil rad etiladi, target qatori transaction ichida lock qilinadi, takroriy grant auditni takrorlamaydi. DRF capability permission class’lari qo‘shildi; profil PATCH noma’lum capability’ni rad etishi testlandi. `pytest -q` — 73 passed; `manage.py check`, schema validation, migration drift va `git diff --check` o‘tdi. |
| P018 | verified | `manage_accounts` ruxsatli operator uchun `POST /api/v1/accounts/` va `POST /api/v1/accounts/<id>/reset-password/` qo‘shildi. Pupil/teacher yaratish, serverda validatorlardan o‘tuvchi tasodifiy vaqtinchalik parol, `set_password()` hash va `must_change_password=True` bilan bajariladi; vaqtinchalik parol faqat `Cache-Control: no-store` javobida bir marta qaytadi. Reset password hashini yangilab avvalgi sessiyalarni keyingi so‘rovda Django auth hash tekshiruvi bilan bekor qiladi. Privilege/enrollment va reset request body field’lari rad etiladi; lifecycle audit actor/target/action/vaqtni saqlaydi, request body yoki parol log/auditga yozilmaydi. `pytest -q` — 79 passed (+9 subtests); Django check, schema validation, migration drift va `git diff --check` o‘tdi. |
| P019 | verified | `learning.Topic` va initial migration qo‘shildi; termning academic year bilan mosligi model `clean()`da, `(course, term, order)` unikaligi DB constraint’da tekshiriladi. Read-only DRF ViewSet `/api/v1/courses/<id>/topics/` va `/api/v1/topics/<id>/` GET’larini beradi; faqat so‘ralgan route’lar ochilgan, global topic list yo‘q. Course queryset teacher assignment/pupil enrollment bilan cheklangan, pupil draft list/detail’dan 404 oladi, scope’dan tashqari parent course 404 qaytaradi. `pytest -q` — 85 passed (+9 subtests); `manage.py check`, `spectacular --validate`, migration drift va `git diff --check` o‘tdi. |
| P020–P090 | pending | Keyingi prompt faqat alohida so‘rovdan keyin boshlanadi. |
| P023-A…P023-F | pending | P023’dan keyingi alohida katalog/roster promptlari. |
| P081-A…P081-B | pending | P081’dan keyingi proxy/TLS va storage promptlari. |

## Keyingi prompt

**P020 — Teacherning bitta draft mavzu yaratishi** — P019 qabul mezonlari tekshirildi;
navbatdagi prompt faqat alohida so‘rovdan keyin boshlanadi.

## Qaydlar

- P001 auditida LMS biznes modellari, endpointlar va testlar topilmadi; repository
  boshlang‘ich Django skeleti holatida.
- Repository’da Django 6.1.2 va DRF 3.18.3 pinned. `drf-spectacular` 0.30.0’ning
  e’lon qilingan metadata’si Django 6.1’ni sanamaydi; P002’da o‘rnatilgan versiya bilan
  schema smoke-test va P006’da schema validation o‘tdi, ammo bu rasmiy moslik tasdig‘i
  emas.
- P002 va P003’da asosiy DB’ga migrate bajarilmadi. Custom User birinchi migration’dan
  oldin sozlandi; PostgreSQL ulanishi va birinchi asosiy migrate P004 scope’ida.
- P003 oldidan PostgreSQL va repository SQLite ma’lumotlar bazalarida migration jadvali
  yoki user jadvallari yo‘qligi read-only tekshirildi. P003 testlari ajratilgan,
  vaqtinchalik PostgreSQL test bazasida bajarildi; u test yakunida tozalandi.
