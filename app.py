from endpoints import app


if __name__ == "__main__":
    # Run the Flask application
    # You can change host/port or disable debug in production
    app.run(host="0.0.0.0", port=5001, debug=True)


