# Backup va restore runbook (P083)

Backup mavjudligi restore ishlashini isbotlamaydi. Bu loyiha uchun nishonlar:

- **RPO ≤ 1 soat**: rejalashtirilgan backup oralig'i bir soatdan oshmasin.
- **RTO ≤ 4 soat**: restore target yaratish, DB/fayl restore, verification va
  operator tekshiruvi to'rt soat ichida tugashi kerak.

Bu nishonlar hosting, database hajmi, object storage snapshot va tarmoqdan
keyin haqiqiy o'lchov bilan tasdiqlanadi; hozir kafolat sifatida e'lon
qilinmaydi.

## Backup

`scripts/backup_postgres.sh` PostgreSQL custom-format dump, aggregate
`inventory.json`, checksum va local private bytes nusxasini yaratadi. U faqat
`BACKUP_QUIESCED=true` bo'lganda ishlaydi: operator avval upload hamda write
trafficni vaqtincha to'xtatishi kerak. Bu DB metadata va private bytes bir
backup oynasida bo'lishi uchun zarur.

```sh
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=schoollms PGDATABASE=school_lms
export PGPASSWORD=...              # secret store yoki process environmentdan
export PRIVATE_STORAGE_BACKEND=local
export PRIVATE_STORAGE_ROOT=/srv/schoollms/private_files
export BACKUP_DIR=/secure-backups/schoollms
export BACKUP_QUIESCED=true
scripts/backup_postgres.sh
```

Backup directory repositorydan tashqarida, access-restricted va encrypted
bo'lishi kerak. Script credentialni, DB DSNni, attachment byte'larini, payload
va student matnini stdoutga yozmaydi. `inventory.json` faqat aggregate count va
attachment key digestini saqlaydi; u ham backup artifact hisoblanadi.

S3-compatible private storageda DB dump yetarli emas. Operator providerning
private/versioned snapshot yoki aynan backup timestampiga mos private-bucket
copy mexanizmini bajaradi. Public bucket, public ACL yoki production objectni
o'chirish mumkin emas. `backup_postgres.sh` bu backendda ataylab success deb
chiqmaydi, chunki bytes snapshotini provider bajarishi kerak.

## Faqat alohida restore drill

Hech qachon production yoki ishlayotgan DBga restore qilmang. Restore script
faqat yangi `restore_*` yoki `test_*` DB nomini va mavjud bo'lmagan absolute
private directoryni qabul qiladi; target mavjud bo'lsa u rad etadi.

Yangi drill source uchun `seed_restore_drill` faqat bo'sh `test_p083_*` DBda
course, submission, published grade, exam attempt va private PDF yaratadi. U
production yoki mavjud ma'lumotli bazada ishga tushmaydi.

```sh
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=schoollms
export PGPASSWORD=...
export BACKUP_PATH=/secure-backups/schoollms/schoollms-YYYYMMDDTHHMMSSZ
export RESTORE_TARGET_DB=restore_schoollms_drill
export RESTORE_PRIVATE_ROOT=/tmp/restore-schoollms-private
scripts/restore_postgres_test.sh
```

Restore `database.dump` checksumini tekshiradi, yangi DB yaratadi, local private
bytesni yangi directoryga nusxalaydi va `verify_restore --require-records`
orqali course, submission, grade, attempt countlari hamda har bir Attachment
keyiga bytes mavjudligini source inventory bilan solishtiradi. Drill source
faqat shu to'rtta kategoriya bo'yicha sintetik, non-empty yozuvlarga ega bo'lishi
kerak. Ishlayotgan source DBga yozilmaydi; restore target ham avtomatik
o'chirilmaydi.

S3 drillida operator avval alohida private test bucket/snapshot targetini
tayyorlaydi, so'ng `DB_NAME=restore_*`, `PRIVATE_STORAGE_BACKEND=s3` bilan
`python manage.py verify_restore --inventory ... --require-records`ni bajaradi.
Real S3 credential yoki test bucket bo'lmasa buni smoke deb yozmang.

## Drill qaydi

Har hosting drillida start/end UTC, source backup timestamp, target nomi,
DB restore va byte sync vaqti, verification natijasi hamda topilgan xatolarni
restricted operations tizimiga yozing. Secret, token, connection URL, dump yoki
fayl byte'larini ticket/chat/repositoryga qo'ymang.
