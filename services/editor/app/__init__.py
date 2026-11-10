from rp_shared.app_factory import create_service_app


def create_app():
    app = create_service_app("editor")
    # Blueprints are registered here as the service grows (see docs/software_dev_plan.md).
    return app
