from app import create_app

app = create_app(run_migrations=True, consume_events=True)
