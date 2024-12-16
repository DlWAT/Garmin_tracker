from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import time

# Vos identifiants Garmin Connect
USERNAME = "duwat.adrien@gmail.com"
PASSWORD = "Duwat9897."

def connect_to_garmin():
    print("Connexion à Garmin Connect...")
    driver = webdriver.Chrome()
    driver.get("https://connect.garmin.com/modern/workouts")

    try:
        # Accepter les cookies si nécessaire
        try:
            cookies_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accepter')]"))
            )
            cookies_button.click()
            print("Cookies acceptés.")
        except:
            print("Pas de pop-up de cookies détectée.")

        # Attendre que le champ e-mail soit cliquable
        email_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "email"))
        )
        print("Champ e-mail cliquable.")

        # Assurez-vous que l'élément est interactif
        ActionChains(driver).move_to_element(email_input).click().perform()
        email_input.send_keys(USERNAME)
        print("Adresse e-mail saisie.")

        # Remplir le mot de passe
        password_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "password"))
        )
        password_input.send_keys(PASSWORD)
        print("Mot de passe saisi.")

        # Soumettre le formulaire
        password_input.send_keys(Keys.RETURN)
        print("Connexion soumise.")
        time.sleep(5)  # Attendre la redirection après connexion

    except Exception as e:
        print(f"Erreur lors de la connexion : {e}")
        driver.quit()
        raise

    return driver

def main():
    driver = None
    try:
        driver = connect_to_garmin()
        print("Connexion réussie.")
        # Ajoutez ici la logique pour créer un entraînement si nécessaire
    except Exception as e:
        print(f"Une erreur est survenue : {e}")
    finally:
        if driver:
            driver.quit()
        print("Navigateur fermé.")

if __name__ == "__main__":
    main()