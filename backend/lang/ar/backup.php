<?php

declare(strict_types=1);

// ATL-231: `php artisan atlas:backup` ومهمة RunBackup و/api/backup.
return [
    'folder_create_failed' => 'مجلد النسخ الاحتياطي :folder غير موجود وتعذّر إنشاؤه.',
    'file_exists' => 'توجد نسخة احتياطية باسم :file بالفعل.',
    'empty_dump' => 'انتهى pg_dump لكنه كتب ملفًا فارغًا.',
    'pg_dump_exit' => 'انتهى pg_dump برمز الخروج :code',
    'already_running' => 'هناك نسخ احتياطي قيد التشغيل؛ لم يُبدأ شيء جديد.',
    'done' => 'كُتبت النسخة الاحتياطية في :path (:bytes بايت).',
    'failed' => 'فشل النسخ الاحتياطي: :error',
    'queued' => 'سيبدأ النسخ الاحتياطي بعد لحظات.',
];
