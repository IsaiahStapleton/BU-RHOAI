from validate import create_app

webhook = create_app()

if __name__ == '__main__':
    webhook.run()
