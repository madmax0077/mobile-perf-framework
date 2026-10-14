# Critical business journeys

Source: `User_Journeys_Optimized.xlsx`, sheet `Regression_Test_Suite` (1,827 cases, steps in
column M), grouped by journey (`User Journey ID`).

## How the journeys were selected

1. **Consumer app on Android only.** Merchant App, Merchant Portal and Back Office journeys
   (UJ-043 to UJ-078 and most of UJ-079+) run on other apps and are out of scope here.
2. **Money movement and access first.** Sign-in and session, balance, paying a merchant
   (scan to pay, payment code), funding and withdrawing, cards, P2P, bills and top-ups,
   then the engagement journeys that drive them (offers, inbox, stores, loyalty).
3. **Mini Regression Suite = Yes** cases were preferred within each journey.
4. **Existing test account only.** Every test signs in with `APP_TEST_PHONE` / `APP_TEST_PIN`
   / `APP_TEST_OTP`. Users and credentials written in the spreadsheet steps are not used.
5. **No money moves and no account changes.** Payments, top-ups, transfers and PIN changes
   stop before confirmation. Journeys that need a new user (Lite/Full KYC, UJ-002/003), a
   second device, or a backend/merchant counterpart (callbacks, reconciliation, refunds) are
   not automated here.

## Journey to test map

Run them all with:

```text
pytest tests/smoke/test_business_journeys.py tests/smoke/test_login.py tests/smoke/test_wallet.py tests/smoke/test_home_features.py tests/smoke/test_engagement.py tests/smoke/test_profile.py --provider testmu
```

| Journey | Regression cases (examples) | Test | Flow (timed steps) |
|---|---|---|---|
| UJ-001 Login / logout | TST-224 OTP, TST-220 expired session | `test_login.py::test_login_with_phone_pin_otp` | Launch, Profile, Sign in, phone, PIN, OTP, signed-in home |
| UJ-001 Login / logout | TST-220, logout and cancel logout | `test_business_journeys.py::test_uj001_relaunch_and_logout` | Kill and relaunch the app (PIN check timed separately), Profile, Log out > STAY, Log out > LOG OUT, signed-out screen |
| UJ-005 Balance visibility | REB_00016 Display Balance | `test_business_journeys.py::test_uj005_balance_visibility` | Hide balance (eye icon), show balance |
| UJ-020 / UJ-021 Scan to pay (C-Scan-B) | TST-2091, TST-2096 | `test_business_journeys.py::test_uj020_uj022_scan_and_payment_code` | Rebell QR, scanner with default payment method |
| UJ-022 Payment code (B-Scan-C) | TST-2571, TST-2580 | same test | Show payment code, close it, close the scanner |
| UJ-008 PIN management | REB_00012 | `test_business_journeys.py::test_uj008_open_change_pin` | Profile, Security, Change PIN opens (no PIN entered) |
| UJ-010 / UJ-011 / UJ-012 Top up | TST-691, TST-692 | `test_business_journeys.py::test_uj010_uj015_top_up_and_back_to_bank` | Top up opens |
| UJ-015 Unload to bank | TST-3794 | same test | Back to bank opens |
| UJ-014 Add bank card | TST-1994 | `test_home_features.py::test_add_new_card` | Cards & accounts, Add new card form |
| UJ-016 / UJ-018 Send and request P2P | TST-2830 | `test_wallet.py::test_send_and_request` | Send & request sheet, Send money, Request money |
| UJ-006 Transaction history | TST-426 | `test_wallet.py::test_transaction_details` | Latest transaction, details with status |
| UJ-032 PagoPA bill / UJ-033 phone top-up | TST-4010, TST-4089 | `test_wallet.py::test_daily_shortcuts` | Top up phone, Pay bills (PagoPA), Save on utilities |
| UJ-036 Membership cards | TST-5782 | `test_wallet.py::test_loyalty_cards` | Loyalty cards |
| UJ-028 Coupons / offers | TST-3392 | `test_home_features.py::test_check_offers` | Offers, My offers, Discover offers, Flash deals |
| UJ-031 Inbox / UJ-030 notification preferences | TST-2764 | `test_engagement.py::test_notification_centre` | Notification centre, search, tabs, preferences |
| UJ-035 Stores on the map | REB_99938 | `test_engagement.py::test_stores` | Stores, My stores, store page |
| UJ-007 Profile / UJ-013 balance alert | TST-331, TST-1770 | `test_profile.py::test_profile_settings` | Personal details, Balance alert, Subscriptions |

## Known limits of the test account

- **Top up** answers with "Need assistance? We've noticed some unusual activity" for this
  account, so `test_uj010_uj015_top_up_and_back_to_bank` records the time to that answer and
  is reported as skipped (blocked). An unrestricted account runs the full step.
- **Back to bank** opens "Add a new IBAN to unload": the account has no IBAN, so the
  amount and confirmation screens of UJ-015 are not reachable.
- **Offers** depend on the device location; TestMu devices located outside Italy can show
  "Offer not found".
