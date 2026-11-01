<?php

use Illuminate\Foundation\Inspiring;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Schedule;

Artisan::command('inspire', function () {
    $this->comment(Inspiring::quote());
})->purpose('Display an inspiring quote');

// ATL-163: lift `/block <instId> today` entries when their Baghdad day ends.
Schedule::command('atlas:expire-blocks')->everyMinute()->withoutOverlapping();
