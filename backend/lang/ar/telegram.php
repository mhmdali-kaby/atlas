<?php

declare(strict_types=1);

// رسائل تيليجرام (ATL-215). تُرسل نصاً عادياً، والأوقات بتوقيت بغداد.
return [
    'env' => [
        'demo' => 'تجريبي',
        'live' => 'حقيقي',
    ],
    'events' => [
        'proposal_awaiting_approval' => "مقترح بانتظار موافقتك\n:side :inst_id (:kind)، بقيمة :final_notional_usd دولار تقريباً.\nوافق قبل :approval_expires_at (بتوقيت بغداد).",
        'order_filled' => "تم تنفيذ الأمر\n:side :filled_sz :inst_id بسعر :avg_px (:purpose).",
        'stop_hit' => "تم ضرب وقف الخسارة\n:inst_id: أُغلق :sz بسعر :actual_px (الوقف :trigger_px).",
        'target_hit' => "تم بلوغ هدف الربح\n:inst_id: أُغلق :sz بسعر :actual_px (الهدف :trigger_px).",
        'circuit_breaker' => "تم تفعيل قاطع الدائرة: :name\nالأوامر الجديدة متوقفة. :reason",
        'data_stale' => "بيانات السوق قديمة\nالأوامر الجديدة متوقفة. :reason",
        'reconciliation_mismatch' => "اختلاف بين أطلس و OKX\nالأوامر الجديدة متوقفة حتى تنتهي المطابقة. :reason",
        'kill_switch' => "تم استخدام زر الإيقاف\nالتداول متوقف. أوامر أُلغيت: :cancelled، مراكز أُغلقت: :closed، أوامر وقف بقيت: :stops_kept، إخفاقات: :failed.",
        'agent_degraded' => "الوكيل :agent في حالة :state\nيواصل الفريق العمل بدونه.",
        'daily_report' => 'التقرير اليومي ليوم :day جاهز في غرفة العمليات.',
        'news_alert' => "خبر عن أداة تملكها (الأهمية :importance)\n:inst_ids — :headline (:source_name)",
        'backup_failed' => "فشل النسخ الاحتياطي لقاعدة البيانات\nافتح الإعدادات ← النسخ الاحتياطي في أطلس لمعرفة السبب.",
    ],
    'open' => 'افتح: :url',
    'yes' => 'نعم',
    'no' => 'لا',
    'none' => '—',
    'buttons' => [
        'approve' => 'موافقة',
        'reject' => 'رفض',
    ],
    'callback' => [
        'approve_in_atlas' => 'صفقة حقيقية: وافق عليها في Atlas. يمكنك الرفض من هنا.',
        'expired' => 'انتهت الصلاحية',
        'approved' => 'تمت الموافقة وأُرسل الأمر.',
        'approve_failed' => 'لم يُرسل: :reason',
        'rejected' => 'تم الرفض.',
        'reject_failed' => 'لم يُرفض: :reason',
    ],
    'outcome' => [
        'approved' => 'تمت الموافقة من تيليجرام الساعة :time (بتوقيت بغداد).',
        'approve_failed' => 'الموافقة من تيليجرام الساعة :time (بتوقيت بغداد) لم ترسل الأمر: :reason',
        'rejected' => 'تم الرفض من تيليجرام الساعة :time (بتوقيت بغداد).',
    ],
    'reject_reason' => 'رُفض من تيليجرام.',
    'test_message' => 'رسالة اختبار من أطلس: إشعارات تيليجرام متصلة.',
    'not_configured' => 'تيليجرام غير مُعدّ. أضف رمز البوت ومعرّف المحادثة أولاً.',
    'test_failed' => 'لم يقبل تيليجرام رسالة الاختبار. تحقق من رمز البوت ومعرّف المحادثة، ومن أنك أرسلت ‎/start‎ إلى البوت.',
];
