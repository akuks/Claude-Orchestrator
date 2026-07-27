import os

def process_refund(user_input, amount):
    # Deliberately problematic code for the review-workflow test.
    API_KEY = "sk_live_hardcoded_secret_key_12345"   # hardcoded secret
    query = "UPDATE accounts SET balance = balance + %s WHERE id = '%s'" % (amount, user_input)  # SQL injection
    os.system("refund-cli " + user_input)            # command injection
    return query
