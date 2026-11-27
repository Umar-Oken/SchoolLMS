# Health va operational kuzatuv (P082)

## Health endpointlar

- `GET /health/live/` — faqat Django process HTTP so'rov qabul qilayotganini
  bildiradi; DB, Redis, scanner yoki AI'ga ulanmaydi.
- `GET /health/ready/` — DB ulanishi va barcha Django migrationlari qo'llanganini
  tekshiradi. Xato yoki schema drift bo'lsa faqat `503 {"status":"not_ready"}`
  qaytaradi; host, credential, exception yoki migration tafsiloti oshkor
  qilinmaydi.

Readiness ataylab Redis, Celery worker, scanner yoki kelajakdagi AI providerga
bog'lanmaydi: ulardagi degradation API processini noto'g'ri unavailable deb
ko'rsatmasligi kerak. Ular alohida operational signal.

## Request log

Har request JSON logga faqat `request_id`, method, path, status va latency
millisekundini yozadi. Xato uchun faqat exception class yoziladi. Query string,
request body, password, JWT/refresh token, cookie, authorization header,
o'quvchi matni yoki outbox payload loglanmaydi. Bu operational log; biznes
harakatlarning mavjud audit jurnali bilan bir narsa emas.

## Fon ishlari

Operator quyidagini ishga tushiradi:

```sh
python manage.py monitor_background
```

JSON natija quyidagilarni agregat ko'rinishda beradi:

- broker reachability;
- Celery `inspect ping` worker heartbeat va worker soni;
- `pending`, `deferred`, `sent`, `failed` outbox backlog countlari;
- scanner configured/simulated/not-configured holati hamda failed attachment
  soni;
- AI dependency holati (`not_configured` hozirgi holatda).

Broker yoki worker `unavailable` bo'lsa operator Celery/Redis process va tarmoq
sozlamalarini tekshiradi; outbox deferred/failed countlari retry yoki qo'lda
tekshiruv signalidir. Command task payloadi, error detaili, attachment bytesi
yoki shaxsiy ma'lumot chiqarmaydi.

Backup va alohida restore drill tartibi [backup/restore runbookda](BACKUP_RESTORE.md)
yozilgan.
