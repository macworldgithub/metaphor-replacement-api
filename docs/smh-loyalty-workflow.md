# South Morang Hyundai loyalty workflow

The client clarified on 5 November 2026 that loyalty SMS is one way. The PDF's reply branch is superseded for this channel. Customers enquire through the linked landing-page form; they do not create enquiries by replying to SMS.

- sms-campaign: operator portal, tracked customer journey, public form adapter and web unsubscribe confirmation.
- sms-campaign-backend: MongoDB contacts, consent/suppression, campaign sending/delivery, landing enquiries and independent CRM delivery states.
- metaphor-replacement-api: scoped authenticated Chrome reads, verified existing-customer matching and DealerSocket opportunity writing. It does not source the consented SMS audience from CRM snapshots.

The Chrome worker loads backend environment settings first. Default SMS_CHANNEL_MODE is one_way. It skips legacy SMS activity claims unless two_way and DEALERSOCKET_SMS_ACTIVITY_ENABLED=true are both explicitly configured. The backend independently enforces the same restriction. MongoDB, GoodShowroom and campaign credentials are removed from the browser child process environment; only the supported Playwright extension connection token is retained when configured.

DealerSocket continues using Quoc Truong as Primary and Mark Robinson as Secondary. GoodShowroom Sales Floor remains queued with the agreed placeholder until documented API access and instance mappings are supplied. Sales follow-up happens in the CRMs.

Do not modify the original Cranbourne reader or use legacy SMS_KIA_NUMBER as the Hyundai sender. The campaign backend owns its explicit MOBILEMESSAGE_FROM setting. MONGODB_URI and provider credentials may be inherited privately from the root environment via SHARED_ENV_FILE. Do not copy passwords into source, browser scripts or logs.

See ../sms-campaign-backend/PORTAL.md for the canonical workflow, local startup, test commands and production dependencies. Browser sign-in and MFA use the normal authenticated Chrome session; no bypass is used.
