if __name__ == "__main__":
    import multiprocessing

    from tracesurface.cli import app

    multiprocessing.freeze_support()
    app()
