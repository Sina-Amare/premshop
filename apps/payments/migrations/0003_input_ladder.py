import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

PRESERVE = """
CREATE OR REPLACE FUNCTION payments_preserve_refund() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A refund is never deleted' USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'owed' OR NEW.status <> 'executed'
       OR NEW.order_item_id <> OLD.order_item_id OR NEW.amount <> OLD.amount
       OR NEW.created_by_id {compare} OLD.created_by_id
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'An owed refund only becomes executed; an executed one never changes'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""


class Migration(migrations.Migration):
    """The 21-day cancellation is the system's, so its refund has no person behind it.

    Reversing fails while such a refund exists: NOT NULL cannot be restored over it, and
    inventing a creator would rewrite money history.
    """

    dependencies = [
        ("payments", "0002_refund"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="refund",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="ثبت‌کننده",
            ),
        ),
        # `<>` lets an empty creator be filled in later; IS DISTINCT FROM does not.
        migrations.RunSQL(
            PRESERVE.format(compare="IS DISTINCT FROM"), PRESERVE.format(compare="<>")
        ),
        migrations.RunSQL(
            """
            CREATE FUNCTION payments_refund_needs_creator() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                -- The item's latest history row must be the system's own cancellation: an
                -- operator's 21-day cancellation has the same reason and a person behind it.
                IF NEW.created_by_id IS NULL AND NOT EXISTS (
                    SELECT 1 FROM orders_orderitem item
                    WHERE item.id = NEW.order_item_id
                      AND item.cancel_reason = 'input_timeout'
                      AND (
                          SELECT event.to_status = 'CANCELLED' AND event.actor = 'system'
                                 AND event.actor_user_id IS NULL
                          FROM orders_orderitemevent event
                          WHERE event.order_item_id = item.id
                          ORDER BY event.id DESC LIMIT 1
                      )
                ) THEN
                    RAISE EXCEPTION 'A refund without a person is only the 21-day cancellation''s'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER payments_refund_creator
            BEFORE INSERT ON payments_refund
            FOR EACH ROW EXECUTE FUNCTION payments_refund_needs_creator();
            """,
            """
            DROP TRIGGER payments_refund_creator ON payments_refund;
            DROP FUNCTION payments_refund_needs_creator();
            """,
        ),
    ]
