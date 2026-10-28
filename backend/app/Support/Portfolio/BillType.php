<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

/**
 * OKX v5 bill `type` values that the Portfolio summary classifies
 * (GET /api/v5/account/bills, "Bill type": 1 Transfer, 2 Trade, 8 Funding fee;
 * https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-7-days).
 *
 * Fees are NOT a bill type: every bill carries its own `fee` field (negative = charged by
 * OKX, positive = rebate), so fees paid = −Σ fee over all bills. Funding = Σ balChg of
 * type 8 bills (positive = received, negative = paid).
 */
enum BillType: string
{
    case Transfer = '1';
    case Trade = '2';
    case FundingFee = '8';
}
