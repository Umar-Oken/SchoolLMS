# Maktab LMS backend scope

## P001 repository audit

- Repository mavjud; u Django project (`root`) va bitta boshlang‘ich `apps` ilovasidan
  iborat. Hozircha LMS biznes modellari, endpointlar va testlar ishlab chiqilmagan.
- Repository ichida `AGENTS.md`, `README` yoki `docs/` hujjatlari topilmadi.
- Mavjud muhit Python 3.12.3 va Django 6.1.2’dan foydalanadi. `requirements.txt`da
  Django 6.1.2 hamda Django REST framework 3.18.3 pinned. DRF 3.18.3 Django 6.1’ni
  qo‘llashini loyiha metama’lumoti tasdiqlaydi.
- Django 6.1 rasmiy reliz, ammo LTS reliz emas. Django support jadvaliga ko‘ra
  6.1 extended support muddati 2027-yil dekabrda tugaydi. Bu tanlovni keyingi
  bosqichlarda qayta ko‘rib chiqish mumkin; hozirgi scope hujjatdagi kelishuvni qayd
  etadi, loyiha versiyasini o‘zgartirmaydi.
- `drf-spectacular` 0.30.0 mavjud, lekin uning e’lon qilingan Django classifier’lari
  6.1’ni sanamaydi. P002’da o‘rnatilgan versiyada OpenAPI schema validatsiyasi smoke-test
  qilindi; bu yuqori darajadagi rasmiy qo‘llab-quvvatlash bayonoti emas, shuning uchun
  real endpoint schema’lari paydo bo‘lganda yana tekshirish kerak.
- SQLite ma’lumotlar bazasi fayli repository’da mavjud. Bu P001 auditida kuzatilgan
  holat, DB almashtirish yoki mavjud ma’lumotni ko‘chirish bo‘yicha ruxsat emas.

## P008 sinf va fan takliflari

- `Subject` fan katalogidagi barqaror fan ta’rifidir; `CourseOffering` esa fanning
  muayyan `SchoolClass`dagi taklifidir. Masalan, bitta “Matematika” `Subject`i 7-A va
  7-B sinflarining har biriga alohida `CourseOffering` orqali ulanadi.
- `(year, grade_level, section)` sinfni aniqlaydi. `grade_level` 1–11 oralig‘ida
  DB CHECK bilan cheklanadi; sinf va fan taklifi kombinatsiyalari nomlangan
  `UniqueConstraint`lar bilan himoyalanadi.
- AcademicYear→SchoolClass hamda SchoolClass/Subject→CourseOffering FK’lari
  `PROTECT`: tarixga aloqador yozuvlar bog‘langan entity o‘chirilishi sabab yo‘qolmaydi.
  Offeringni o‘chirish o‘rniga `archived_at` belgilanadi; arxivlangan offering ham
  sinf-fan takrorlanish cheklovida qoladi.

## P009 o‘qituvchi biriktirish

- `TeachingAssignment` o‘qituvchi, kurs, boshlanish/tugash sanalari va baholash
  huquqini qayd etadi. Tugash sanasi bo‘sh bo‘lsa, biriktirish davom etmoqda.
- Yangi biriktirish xizmati faqat faol, `teacher` rolidagi foydalanuvchi va
  arxivlanmagan kursga ruxsat beradi. Sana tartibini model tekshiruvi va ma’lumotlar
  bazasidagi CHECK cheklovi himoya qiladi.
- O‘qituvchi almashganda avvalgi ochiq yozuv almashish kunidan bir kun oldin yopiladi,
  so‘ng yangi yozuv yaratiladi. Avvalgi yozuv o‘chirilmaydi; `PROTECT` unga bog‘langan
  o‘qituvchi va kursni o‘chirishdan saqlaydi.

## P010 o‘quvchi sinfi va fan kurslari

- `ClassMembership` o‘quvchining asosiy sinfga a’zoligini, `Enrollment` esa shu
  o‘quvchining muayyan fan kursiga yozilganini ifodalaydi. Faol yozuvlarning tugash
  sanasi bo‘sh; yopilgan yozuvlar sanasi va holati bilan tarixda saqlanadi.
- Ma’lumotlar bazasi bitta faol asosiy sinfni va bitta kursdagi bitta faol yozilishni
  cheklaydi. Bir kursga qayta yozilish mumkin bo‘lishi uchun bu cheklov faqat faol
  holatdagi yozuvlarga taalluqli; yopilgan tarixiy yozuvlar o‘chirilmaydi. Sinfga
  biriktirish xizmati o‘quvchi, sinf va kurs qatorlarini tranzaksiya doirasida
  tekshiradi, eski faol yozuvlarni yopadi va yangi sinfning arxivlanmagan kurslariga
  yozadi. Xatolik bo‘lsa, butun tranzaksiya bekor qilinadi.
- Xizmat faqat oldinga qarab sinf almashishni qo‘llaydi: yangi boshlanish sanasi
  amaldagi a’zolik boshlangan sanadan keyin bo‘lishi, avvalgi a’zolik yoki yopilgan
  kurs yozuvlari davri bilan ustma-ust tushmasligi kerak. Orqaga sana qo‘yilgan
  o‘zgarishlar va murakkab davr kesishmalarini xizmat rad etadi; tarixiy sanalarni
  qayta hisoblash ushbu bosqichga kirmaydi.
- Rasmiy tayanchlar: Django tranzaksiyalari
  (<https://docs.djangoproject.com/en/5.2/topics/db/transactions/>) va PostgreSQL
  cheklovlari (<https://www.postgresql.org/docs/current/ddl-constraints.html>).

## P019 mavzular va read API

- `CourseOffering` muayyan sinf-fan kursini ifodalaydi; `learning.Topic` esa shu
  kursning o‘quv yiliga tegishli choragidagi (`Term`) tartiblangan dars mavzusidir.
- `Topic.state` `draft` yoki `published`; `published_at` nashr vaqtini saqlaydi.
  `(course, term, order)` DB constraint bilan unikal. Term va kursning o‘quv yili
  mosligi model `clean()` validatsiyasida tekshiriladi.
- Read-only API `/api/v1/courses/<id>/topics/` va `/api/v1/topics/<id>/` orqali
  mavzularni qaytaradi. Teacher faqat biriktirilgan kurs mavzularini, pupil faqat
  enrollment mavjud kurslarning published mavzularini ko‘radi. Parent scope va
  detail scope mos kelmasa, API 404 qaytaradi. Pupil draft detail’ni ID bilan ham
  ololmaydi.

## P020 draft mavzu yaratish

- `POST /api/v1/topics/` oddiy `CreateModelMixin` orqali topic yaratadi. Faqat faol
  assignment’i bor teacher kursi tanlanadi; pupil so‘rovi 403, begona yoki
  arxivlangan kurs 404 qaytaradi.
- Client `course`, `term`, `title`, `objective`, `order` yuboradi. Serializer
  termning course o‘quv yiliga mosligini tekshiradi; `state`, `published_at`,
  `teacher` va boshqa noma’lum maydonlar input sifatida rad etiladi. Server `draft`
  holati va bo‘sh `published_at` qiymatini o‘rnatadi.
- Faqat `POST /topics/` ochilgan; course topic list va topic detail GET’lari
  saqlanadi. Update, delete, publish, resource va boshqa write amallari yo‘q.

## P021 draft tahriri va arxivlash

- Faqat biriktirilgan faol teacher draft mavzuni `PATCH /api/v1/topics/<id>/`
  orqali `term`, `title`, `objective`, `order` maydonlarida tahrirlaydi. Course,
  state, publish metadata, archive metadata va noma’lum maydonlar input sifatida
  rad etiladi; PUT, published update, begona teacher update hamda DELETE yo‘q.
- `POST /api/v1/topics/<id>/archive/` `archived_at` vaqtini yozadi; DB qatori
  o‘chmaydi. Archived mavzular teacher/pupil active list/detail’dan chiqariladi,
  pupil published mavzular ro‘yxatidan ham olinadi. Arxivni qaytarish bu scope’da yo‘q.
- Archive kelajakdagi baho/topshiriq/submission FK tarixini o‘chirmaydi yoki
  o‘zgartirmaydi. Keyingi modullar tarixiy yozuvlarni `PROTECT` kabi FK siyosati
  bilan saqlashi kerak; arxivlangan mavzuga yangi faol ishlar ochilmaydi.

## P022 mavzuni nashr qilish

- `POST /api/v1/topics/<id>/publish/` tor `publish_topic` xizmati orqali ishlaydi.
  Faqat faol assignment’i bor teacher o‘zining arxivlanmagan kurs mavzusini nashr qila oladi.
- Draft publish’dan oldin title va objective bo‘sh emasligi hamda term yili course
  yiliga mosligi tekshiriladi. Bitta transaction `state=published` va `published_at`
  ni birgalikda yozadi. Published topic qayta publish qilinganda o‘zgarmagan natija
  va dastlabki timestamp qaytadi; takroriy yozuv yaratilmaydi.
- O‘quvchiga mavzu publish bo‘lgach, mavjud enrollment/topic read scope’i orqali
  ko‘rinadi. Notification, Celery, resource avtomatik nashri va AI amali qo‘shilmagan.

## Loyiha chegarasi

- **Maqsad:** bitta maktabning o‘quv jarayoniga mo‘ljallangan LMS backendini bosqichma-
  bosqich qurish. Ushbu fayl tayyor backend kodi emas; u P000 va P001–P090 promptlari
  bilan bajariladigan ishlar rejasidir.
- **Foydalanuvchilar:** faqat `pupil` va `teacher` biznes rollari. `manage_accounts` va
  `manage_academics` — tanlangan teacherlarga beriladigan capability’lar; yangi biznes
  rollari emas. Django superuser texnik boshqaruv vositasi: `is_staff` Django adminiga
  kirishni, `is_superuser` esa Django ruxsatlarini cheklovsiz olishni bildiradi; ikkalasi
  ham `role` maydonining o‘rnini bosmaydi. Superuser uchun ham `pupil` yoki `teacher`
  biznes roli alohida ko‘rsatiladi.
- **Arxitektura:** mavjud repository ichidagi Django backend. Reja modulli monolit
  tuzilmani, ehtiyoj tug‘ilganda ilovalar qo‘shishni va API contract’ini OpenAPI orqali
  hujjatlashtirishni ko‘zlaydi.
- **Autentifikatsiya:** session auth va CSRF-himoyalangan login/logout. Ommaviy
  ro‘yxatdan o‘tish, JWT va wildcard CORS scope’ga kirmaydi.
- **Maqsadli modul guruhlari:** accounts/auth, academics/catalog, learning/topics,
  files/resources, assignments/submissions, assessments/grades, scheduling/attendance,
  communications/background jobs, dashboards/API sifati va ixtiyoriy AI qoralama.
  Ular P000 dagi dependency ketma-ketligiga muvofiq, faqat tegishli promptda quriladi.
- **Frontend:** React, Vite, TypeScript, UI/layout, frontend routing/state va mobil ilova
  bu ish scope’iga kirmaydi. Frontend keyinchalik tasdiqlangan API/auth/file contractiga
  tayangan holda alohida amalga oshiriladi.
- **Scope’dan tashqarida:** parent roli, multi-tenant SaaS, davlat integratsiyasi,
  GraphQL, microservices, Kubernetes, public registration, avtomatik jadval solveri,
  video transcode va P000’da keyinga qoldirilgan boshqa kengaytmalar.

## Ishlash va data qoidalarining tayanchlari

- Mavjud mos kodni saqlash; har promptning scope’i va qabul mezonidan chetga chiqmaslik.
- Accounts user modeli `AbstractUser` standart username/auth maydonlarini saqlaydi va
  `pupil` yoki `teacher` biznes rolini majburiy talab qiladi. `preferred_language` uchun
  ruxsatli tillar ro‘yxati hali kelishilmagani sababli qiymat dastlab bo‘sh bo‘lishi mumkin.
- Custom User birinchi migratsiyadan oldin belgilanadi. Mavjud migration tarixi yoki
  DB’ni reset qilish va migration’larni o‘chirish mumkin emas.
- Session auth, queryset scope, object permission va parent FK validatsiyasi birga
  qo‘llanadi. List/detail va write ruxsatlari foydalanuvchi roli va tegishli
  enrollment/assignment bilan cheklanadi.
- Sana-vaqt UTC aware saqlanadi, maktab lokal ko‘rinishi `Asia/Tashkent`; ball `Decimal`.
  Baho jami serverda hisoblanadi.
- Published kontent/rubric va submitted revision tarixi izsiz almashtirilmaydi.
  Muhim DB yozuvlari transaction va zarur bo‘lsa row lock’dan foydalanadi.
- Fayl private storage’da saqlanadi; faqat permission va clean scan’dan keyin ochiladi.
  Redis asosiy baho/javob ma’lumotlarining yagona saqlash joyi emas.
- Testlarda faqat sintetik ma’lumot ishlatiladi. Secret, session, login body va pupil
  matni loglanmaydi.

## P001 qabul holati va keyingi kirish sharti

P001 uchun backend scope va progress shabloni yozildi. P002’da mavjud Django skeleton
saqlandi, environment-driven secret/configuration kiritildi va lock qilingan dependency
ro‘yxati tekshirildi. `drf-spectacular` metadata’si Django 6.1’ni rasman tasdiqlamaydi;
o‘rnatilgan versiyada schema smoke-test o‘tdi, ammo endpoint contract’lari paydo bo‘lganda
moslik yana tasdiqlanishi kerak. P003’da custom User va boshlang‘ich migration fayli
yaratildi; model testlari muvaffaqiyatli o‘tdi. Asosiy PostgreSQL bazaga migration
qo‘llanmadi.

## Manbalar

- Django download/support jadvali: <https://www.djangoproject.com/download/>
- Django REST framework requirements: <https://www.django-rest-framework.org/>
- drf-spectacular package metadata: <https://pypi.org/project/drf-spectacular/>
- Asosiy loyiha talablari: `Maktab_LMS_plan.txt`, P000 va P001.
