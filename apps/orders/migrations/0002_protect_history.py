from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]

    operations = [
        migrations.RunSQL(
            """
            CREATE FUNCTION orders_preserve_event() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Order history is append-only' USING ERRCODE = '23514';
            END;
            $$;
            CREATE TRIGGER orders_event_immutable
            BEFORE UPDATE OR DELETE ON orders_orderitemevent
            FOR EACH ROW EXECUTE FUNCTION orders_preserve_event();
            """,
            """
            DROP TRIGGER orders_event_immutable ON orders_orderitemevent;
            DROP FUNCTION orders_preserve_event();
            """,
        ),
    ]
