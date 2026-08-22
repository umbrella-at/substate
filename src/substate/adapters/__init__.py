"""Provider adapters. Each one turns a provider's webhook into a `ParsedPayment`.

Adapters live behind `substate.payments.PaymentWebhook` and are imported by
name, so a project that speaks to one provider never loads the others.
"""
