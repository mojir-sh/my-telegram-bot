import random
import string

def generate_key(length=6):
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))
