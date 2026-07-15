import random
import string

# --- Configuration ---
# The total number of unique codes to generate for the entire squad.
NUM_PLAYERS = 50
# --- End of Configuration ---

def generate_unique_code(existing_codes):
    """Generates a unique code in the format 'ABC-123'."""
    while True:
        letters = ''.join(random.choices(string.ascii_uppercase, k=3))
        numbers = ''.join(random.choices(string.digits, k=3))
        code = f"{letters}-{numbers}"
        if code not in existing_codes:
            existing_codes.add(code)
            return code

def main():
    """
    Generates unique registration codes for all players and outputs them
    to a human-readable text file and a ready-to-use SQL script.
    """
    print(f"Generating {NUM_PLAYERS} unique registration codes...")
    
    generated_codes = set()
    codes = []

    for _ in range(NUM_PLAYERS):
        codes.append(generate_unique_code(generated_codes))

    print(f"Successfully generated {len(codes)} unique codes.")

    # --- Create the human-readable file for distribution ---
    try:
        with open("codes_for_distribution.txt", "w") as f:
            f.write("--- PLAYER REGISTRATION CODES ---\n")
            for code in codes:
                f.write(f"{code}\n")
        print("Created 'codes_for_distribution.txt' with codes to hand out to players.")
    except IOError as e:
        print(f"Error writing to codes_for_distribution.txt: {e}")
        return

    # --- Create the SQL script for the database ---
    try:
        with open("insert_codes.sql", "w") as f:
            f.write("-- SQL script to insert registration codes into the database.\n")
            f.write("-- Copy and run this entire script in your pgAdmin Query Tool.\n\n")
            
            f.write("-- Player Codes (all set to 'rookie' by default)\n")
            for code in codes:
                # All players are assigned the 'rookie' role by default.
                # Change 'rookie' to 'veteran' here if needed for a specific group.
                f.write(f"INSERT INTO registration_codes (code, player_role) VALUES ('{code}', 'rookie');\n")
        
        print("Created 'insert_codes.sql'. Run this file in pgAdmin to populate your database.")
    except IOError as e:
        print(f"Error writing to insert_codes.sql: {e}")

if __name__ == "__main__":
    main()
