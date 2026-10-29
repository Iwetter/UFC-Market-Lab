"""Container entrypoint: one PAPER writer with a private dashboard."""
import os
from pathlib import Path

from .app import create_app
from .domain import Settings
from .operations import writer_lease


def main():
    import uvicorn
    cfg=Settings.model_validate_json(Path("config/default.json").read_text())
    cfg=Settings.model_validate(cfg.model_dump()|{"source":os.getenv("UFC_SOURCE","kalshi"),"database":os.getenv("UFC_DATABASE","/app/data/kalshi.sqlite3"),"mode":"paper"})
    with writer_lease(cfg.database):
        app=create_app(cfg,operations=True)
        server=uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=8000,workers=1,access_log=False))
        app.state.on_runtime_failure=lambda:setattr(server,"should_exit",True)
        server.run()
        if app.state.engine.health.get("engine_fault"):raise SystemExit(1)


if __name__ == "__main__":main()
