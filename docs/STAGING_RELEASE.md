# Staging qabul va rollback runbook (P084)

Staging productiondan ajratilgan, sintetik yoki ruxsat berilgan test ma'lumotli
release muhiti. U deploy/migration/proxy va operatsion oqimni qabul qilish
uchun; production rollout emas. Smoke qisqa funksional dalil, load esa faqat
cheklangan profilning o'lchovi — ikkalasi ham production hajmi yoki SLA
kafolati emas.

## Qabul qilingan lokal release

- Kod asosiy Git revision: `eafa330`; real release uchun aynan immutable commit
  SHA/tag yoziladi, dirty worktree release versiyasi bo'la olmaydi.
- Lokal Gunicorn: `127.0.0.1:8020`, 2 worker, sintetik `test_p083_*` DB,
  PostgreSQL port `5433`; process testdan keyin toza to'xtatildi.
- Multi-worker JWT uchun barcha workerlarda bir xil `DJANGO_SECRET_KEY` process
  environmentidan berilishi shart. Productionda bu qiymat allaqachon majburiy;
  lokal DEBUG fallback random kalit bilan ko'p worker ishga tushirmang.
- Nginx konfiguratsiyasi P081-A'da tayyor, ammo ushbu hostda Nginx binary,
  TLS sertifikati va tashqi hosting authorization yo'q. Shuning uchun bu
  lokal, reviewable qabul; HTTPS/Nginx smoke deploy operatorining ochiq qadami.

2026-11-27 lokal smoke JWT login orqali quyidagini 200/201 bilan tekshirdi:
course, published topic resource, pupil gradebook, exam, notification,
teacher return, pupil draft update va submit. Dastlabki synthetic grade return
qilinib, yangi draft versiyasi yuborildi; bu mavjud max-attempt qoidasi va
real submit oqimini chetlab o'tmaydi.

## Cheklangan load o'lchovi

`load/p084_locustfile.py` token va submit parametrlarini faqat runtime
environmentdan oladi; repository yoki logda credential/student matni yo'q.
Profil 30 concurrent sintetik userni 12 soniya ishlatadi: 4:1 nisbatda course
read va avval qabul qilingan bir xil idempotency-key bilan submit retry.

| Yo'l | So'rov | Xato | p95 | Maqsad |
|---|---:|---:|---:|---|
| `GET /api/v1/courses/` | 1849 | 0 | 64 ms | `<500 ms`, shu lokal o'lchovda o'tdi |
| idempotent `POST .../submit/` | 474 | 0 | 60 ms | `<1 s`, shu lokal o'lchovda o'tdi |

Bu natija 2 Gunicorn worker, local loopback, PostgreSQL 5433 va kichik
synthetic datasetga tegishli. Network, Nginx/TLS, real S3/AI/scanner, katta
ma'lumot va real browser traffic o'lchanmagan. Yangi release uchun load qayta
ishlatilishi mumkin:

```sh
STAGING_BEARER_TOKEN=... \
STAGING_SUBMIT_PATH=/api/v1/assignments/123/submit/ \
STAGING_SUBMIT_DRAFT_VERSION=2 \
STAGING_SUBMIT_KEY=release-review-key \
locust --headless --host https://staging.example.edu --users 30 --spawn-rate 30 \
  --run-time 12s --csv /restricted/path/p084-load -f load/p084_locustfile.py
```

Faqat disposable staging user/assignment va yangi idempotency keydan
foydalaning. `submit` biznes kvotasini throttle bilan almashtirmang.

## Release qadamlari

1. Immutable commit/tag, dependency lock va CI yashil ekanini qayd eting.
2. Staging backup/restore runbookidagi oxirgi restore dalilini tekshiring;
   P083 synthetic restore `verify_restore --require-records`dan o'tgan.
3. Oldingi va yangi kodda `python manage.py migrate --plan`ni ko'ring. Faqat
   backward-compatible, odatda qo'shimcha migrationni avval qo'llang;
   destructive/contract-breaking migration alohida release rejasini talab
   qiladi.
4. Locked dependency bilan install, bitta release processida
   `python manage.py migrate --noinput`, so'ng `gunicorn --check-config`.
   Migrationni har worker startida bajarmang.
5. Gunicornni restart qiling, `/health/live/` va `/health/ready/`ni tekshiring.
   Nginx/TLS configured bo'lsa P081-A HTTPS/CSRF smoke'ini ham bajaring.
6. Login → course → resource → submit → grade → exam → notification smoke va
   ushbu cheklangan 30-user loadni qayta bajaring. AI keyingi darsdan keyin
   yana final gate bo'ladi; uning available emasligi readinessni yiqitmaydi.

## Rollback

1. Qabul xatosi bo'lsa trafficni oldingi sog'lom Gunicorn release'iga qaytaring
   va `/health/ready/` hamda minimal login/read smoke'ini tekshiring.
2. Oldingi kodni faqat yangi migrationlar bilan backward-compatible ekanligi
   tasdiqlansa ishga tushiring. Migrationni ko'r-ko'rona orqaga qaytarmang.
3. Migration yoki data contract irreversible bo'lsa rollback o'rniga forward
   fix qiling. Oxirgi chora sifatida P083 runbooki bo'yicha alohida restore
   targetda verify qilingan backupdan recovery qarorini operator beradi.
   Ishlayotgan production DB yoki private bytesni overwrite qilmang.
4. Incidentda release SHA, migration holati, request ID, health, CI/restore va
   smoke dalilini restricted operations tizimiga yozing; token, cookie,
   password, dump yoki student content yozmang.

## Ochiq blockerlar

- Bu hostda Nginx binary/TLS credentiali hamda tashqi staging publish vakolati
  yo'q; real HTTPS proxy acceptance hali deploy operatorida.
- Real configured S3 test bucket va external scanner/AI integration smoke
  bajarilmagan; mocks real integration dalili emas.
- P083 RPO <=1 soat/RTO <=4 soat nishon, hosting hajmi va tarmog'ida o'lchanib
  tasdiqlanishi kerak.
