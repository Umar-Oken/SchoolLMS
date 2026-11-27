# AI job boundary (P085)

AI job — bu tashqi model chaqiruvi emas, balki keyinchalik worker olishi mumkin
bo'lgan, versiyalangan ichki request. `queued` holatidagi job response vaqtini
tashqi providerga bog'lamaydi. P085da worker, real provider call, quota, API
va AI tutor yo'q.

## Ma'lumot va ruxsat chegarasi

`AIJob` actor, course, resource, resource versiyasi, prompt versiyasi,
provider model nomi, token soni, xato va state'ni saqlaydi. Yaratish xizmati
faqat faol assignment'i bo'lgan teacherning o'z course'iga tegishli resource'ni
qabul qiladi; course va resource relationship'i model `clean()`ida ham
tekshiriladi. `AIJobCreateSerializer` actor/course/resource_versionni clientdan
olmaydi — ular server tomonidan chiqariladi.

Resource versiyasi snapshot bo'lib qoladi, shuning uchun keyingi resource
o'zgarishi qaysi inputdan draft olinganini yashirmaydi. `AIJobResultDraft`
structured question text, choices, correct choice index va explanationni
saqlaydi. U `Grade`, `Attempt`, submission yoki gradebookga FK/yozuvchi yo'lga
ega emas: AI natijasi faqat inson review qiladigan draft.

## State va provider

Ruxsatli job state'lari: `queued`, `running`, `review`, `failed`, `approved`,
`rejected`. PostgreSQL CHECK constraint noto'g'ri state va manfiy tokenni rad
qiladi. P085dagi result persist xizmati faqat `running → review` o'tishini
bajaradi; approve/reject va real queue worker keyingi scopega qoldirilgan.

`AIProvider` Protocol structured `StructuredQuestionDraft` qaytaradi. Default
provider `disabled`; haqiqiy model adapteri qo'shilmagan. `FakeProvider`
deterministik test double bo'lib, faqat `AI_PROVIDER=fake` va non-production
muhitda ishlaydi. Productiondagi shu setting `ImproperlyConfigured` bilan rad
etiladi. Mock muvaffaqiyati real provider, xavfsizlik, pedagogik sifat yoki
external integration smoke dalili emas.

## PDF text extraction (P087)

`ai.pdf_extraction.extract_pdf_text()` kelajakdagi AI worker uchun faqat faol
assignment doirasidagi teacherning aynan so'ralgan `resource_id` va
`resource_version`idagi published PDF'ini qayta tekshiradi. Attachment `clean`,
`application/pdf`, shu teacherga tegishli va scanner tasdiqlagan SHA-256 hashga
ega bo'lishi kerak.
Bytes faqat `storages['private'].open()` orqali o'qiladi; public URL, URL fetch
yoki tashqi storage link ishlatilmaydi. Har qaytgan `PDFTextChunk` source ID,
resource version, SHA-256 va 1-dan boshlangan sahifa raqamini saqlaydi.

Text-PDFdagi selectable matn `pypdf` orqali olinadi. Scan/image-only PDFda
extractable text bo'lmasa aniq `no_text` xatosi qaytadi: OCR boshlanmaydi.
Fayl 10 MiB, 100 sahifa va jami 20 000 belgidan oshmaydi; sahifalar/parse
xatosi mos ravishda `pdf_too_large`, `pdf_too_many_pages` yoki
`pdf_parse_error` bo'ladi. 20 000 belgi chegarasida natija kesiladi va
`truncated=true` belgilanadi.

PDFdan olingan matn ishonchsiz hujjat mazmuni, tizim yoki worker ko'rsatmasi
emas. Undagi “instruction”, link yoki promptga o'xshash satrlar bajarilmaydi.
Bu adapter model chaqiruvi, vector DB/RAG, OCR, savol yaratish/publish yoki
ma'lumotlarni yozishni bajarmaydi.

## Draft worker (P088)

`ai.tasks.generate_question_drafts` Celery `ai` queue'da queued jobni lock bilan
claim qiladi va faqat beshta single-choice qoralamani `review`ga saqlaydi.
Har birida variantlar, bitta `correct_choice_index`, izoh va PDF snapshotidagi
haqiqiy `source_id`/version/SHA-256/page citation bo'lishi majburiy. DRF
serializeri JSON schema, soni va citationning aynan extraction chunkiga
tegishliligini tekshiradi. Bu citation **mavjudligini** isbotlaydi, savolning
mazmunan o'sha sahifadan kelganini avtomatik baholamaydi; teacher review shart.

Task `acks_late`, worker-loss reject, 60 soniyali provider timeout contracti va
faqat ikki retryga ega. Duplicate event `review`/`failed` jobni qayta providerga
jo'natmaydi va DB unique `(job, position)` draftlarni takrorlamaydi. PII
(foydalanuvchi profili, submission, baho) providerga yuborilmaydi; PDF text va
provenancegina yuboriladi. Natija avtomatik savol banki yoki bahoga yozilmaydi.

Hozir real provider konfiguratsiyasi berilmagan: `AI_PROVIDER=disabled` default,
`fake` esa non-production deterministic integration mockidir. Shuning uchun real
HTTPX/SDK adapteri ataylab tanlanmadi. Real ishga tushirish blockeri: provider
nominig, endpoint/model, secret env var va data-processing tasdig'i kerak.
