# Private S3-compatible storage (P081-B)

Default `PRIVATE_STORAGE_BACKEND=local` local private adapterni saqlaydi.
`PRIVATE_STORAGE_BACKEND=s3` tanlanganda `PRIVATE_S3_BUCKET` majburiy va
django-storages/boto3 S3-compatible endpointga Django Storage API orqali
ulanadi. Adapter bucket yoki credential yaratmaydi, eski local objectlarni
ko'chirmaydi va hech narsani ommaviy qilmaydi.

Bucket policy private bo'lishi kerak: anonymous `GetObject`, public ACL hamda
object list ruxsatlari bo'lmasin. Process credentiali faqat shu private bucket
uchun zarur `GetObject`/`PutObject` huquqlariga ega bo'lsin. Object key UUID;
original filename faqat DB metadata va signed response content-dispositionda
ishlatiladi. Scanner worker ham ayni `storages['private'].open()` interface
orqali bytes o'qiydi.

## Signed download

`GET /api/v1/attachments/<id>/download/` avval mavjud object-level selector va
`scan_status=clean`ni tekshiradi. Local adapterda u Django stream response
qaytaradi. S3 adapterda esa 30–300 soniyali (default 60) signed `GET` URLga
302 redirect qiladi. Begona pupil, pending/infected/failed attachment signed
URL olmaydi. URL berilgandan keyin uning qisqa muddatli bearer xarakteri bor;
uni log, chat yoki browser historyga tarqatmang.

CORS browserning cross-origin qoidasi, authorization esa server-side selector
va bucket policydir. Signed download uchun public bucket yoki permissive CORS
kerak emas. Frontend upload widgeti va katta multipart upload bu scope'da yo'q.

## Real smoke blokiri

Bu repositoryda S3 credential yoki configured test bucket yo'q. Unit testlar
signed URLni SDK mock/mahalliy signature bilan tekshiradi; ular real S3 smoke
emas. Faqat operator private test bucket credentialsini secret store orqali
berganidan keyin alohida muhitda upload → scanner → clean download URL oqimini
tekshirsin. Production/old bucketga smoke upload yoki delete qilmang.
