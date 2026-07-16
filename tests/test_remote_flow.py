import re
import time
import requests
import subprocess

BASE_URL = "https://fin-web.tail0af4fe.ts.net"

def get_remote_otp():
    # Retrieve the logs of the last 15 seconds to avoid matching stale OTPs
    cmd = 'ssh financial-lab "docker logs --since 15s ai_research_web"'
    output = subprocess.check_output(cmd, shell=True).decode("utf-8")
    matches = re.findall(r"Your registration verification code is (\d{6})", output)
    if matches:
        return matches[-1]
    return None

def get_remote_document_ids():
    cmd = 'ssh financial-lab "docker exec ai_research_web python manage.py shell -c \\"from documents.models import SourceDocument; print([(d.visibility_scope, str(d.id)) for d in SourceDocument.objects.all()])\\""'
    output = subprocess.check_output(cmd, shell=True).decode("utf-8")
    import ast
    for line in output.splitlines():
        if line.startswith("[(") or line.startswith("["):
            return dict(ast.literal_eval(line))
    return {}

def run_test():
    session = requests.Session()
    
    # Step 1: GET Registration Page to retrieve CSRF token
    reg_url = f"{BASE_URL}/auth/register/"
    r_get = session.get(reg_url, verify=True)
    assert r_get.status_code == 200, f"GET register failed: {r_get.status_code}"
    
    csrf_token = session.cookies.get("csrftoken")
    assert csrf_token, "No CSRF token found in cookies!"
    print(f"[1] Retrieved CSRF Token: {csrf_token}")
    
    # Step 2: POST Student Registration to trigger OTP
    data = {
        "student_id": "410410123",
        "fju_cloud_email": "410410123@cloud.fju.edu.tw",
        "backup_email": "student_backup@example.com",
        "phone_number": "0912345678",
        "captcha_token": "test-pass", # Debug CAPTCHA token
        "csrfmiddlewaretoken": csrf_token,
    }
    headers = {
        "Referer": reg_url,
    }
    
    r_post = session.post(reg_url, data=data, headers=headers)
    print(f"POST Response Code: {r_post.status_code}")
    print(r_post.text[:2000])
    assert r_post.status_code == 200, f"POST register failed: {r_post.status_code}"
    # The response should tell us we need to verify OTP
    assert "verification" in r_post.text or "OTP" in r_post.text or "驗證" in r_post.text, "Response didn't prompt for OTP"
    print("[2] OTP triggered successfully.")
    time.sleep(2)
    
    # Step 3: Retrieve OTP from remote Docker console log
    otp = get_remote_otp()
    assert otp, "Failed to retrieve OTP from remote docker logs!"
    print(f"[3] Retrieved OTP from remote logs: {otp}")
    
    # Step 4: POST Verification request to complete registration
    verify_url = f"{BASE_URL}/auth/register/verify/"
    
    # Get new CSRF token if updated
    csrf_token = session.cookies.get("csrftoken") or csrf_token
    
    # We need to extract the hidden registration_id from the page, or retrieve it.
    # The verify page has a form with name="registration_id" or similar.
    # Let's search for registration_id uuid in r_post.text
    reg_id_match = re.search(r'name="registration_id"\s+value="([a-f0-9\-]+)"', r_post.text)
    assert reg_id_match, "Registration ID not found in verify page!"
    registration_id = reg_id_match.group(1)
    print(f"[4] Found Registration ID: {registration_id}")
    
    verify_data = {
        "registration_id": registration_id,
        "otp": otp,
        "new_password1": "secureStudentPass123!",
        "new_password2": "secureStudentPass123!",
        "captcha_token": "test-pass", # Add debug captcha token
        "csrfmiddlewaretoken": csrf_token,
    }
    headers["Referer"] = verify_url
    r_verify = session.post(verify_url, data=verify_data, headers=headers)
    
    print(f"Verify Response URL: {r_verify.url}")
    print(f"Verify Response Code: {r_verify.status_code}")
    print(r_verify.text[:2000])
    
    assert r_verify.status_code == 200, f"Verification failed: {r_verify.status_code}"
    assert "410410123" in r_verify.text, "Student ID not displayed on profile page after verify redirect!"
    print("[5] Student OTP verification and registration succeeded!")
    
    # Step 5: Test explicit logout and login with new account
    csrf_token = session.cookies.get("csrftoken") or csrf_token
    logout_url = f"{BASE_URL}/auth/logout/"
    session.post(logout_url, data={"csrfmiddlewaretoken": csrf_token}, headers={"Referer": f"{BASE_URL}/profile/"})
    
    # Verify we are logged out (profile page should redirect to login/admin page or fail)
    r_profile_anon = session.get(f"{BASE_URL}/profile/")
    assert r_profile_anon.status_code != 200 or "Account profile" not in r_profile_anon.text, "Profile still accessible after logout!"
    print("[6] Logged out successfully.")
    
    # Re-retrieve login page to get fresh CSRF token
    login_url = f"{BASE_URL}/auth/"
    session.get(login_url)
    csrf_token = session.cookies.get("csrftoken")
    
    login_data = {
        "intent": "login",
        "identifier": "410410123",
        "password": "secureStudentPass123!",
        "captcha_token": "test-pass",
        "csrfmiddlewaretoken": csrf_token,
    }
    r_login = session.post(login_url, data=login_data, headers={"Referer": login_url})
    assert r_login.status_code == 200, f"Login post failed: {r_login.status_code}"
    
    # Verify that we are logged in and can view the profile page or user info
    profile_url = f"{BASE_URL}/profile/"
    r_profile = session.get(profile_url)
    assert r_profile.status_code == 200, "Profile page inaccessible after login!"
    assert "410410123" in r_profile.text, "Student ID not displayed on profile page!"
    print("[7] Unified Login and Profile page access verified successfully!")

    # Step 6: Test document access controls
    docs = get_remote_document_ids()
    public_doc_id = docs.get("public")
    student_doc_id = docs.get("student")
    
    assert public_doc_id, "Public document not found!"
    assert student_doc_id, "Student document not found!"
    
    print(f"Testing public document {public_doc_id} and student document {student_doc_id}")
    
    # 6a. Try downloading as Anonymous user
    anon_session = requests.Session()
    r_pub_anon = anon_session.get(f"{BASE_URL}/documents/{public_doc_id}/download/")
    assert r_pub_anon.status_code == 200, f"Anonymous failed to download public doc: {r_pub_anon.status_code}"
    print("[8] Anonymous allowed to download public document.")
    
    r_stud_anon = anon_session.get(f"{BASE_URL}/documents/{student_doc_id}/download/")
    # Anonymous should be redirected to login or blocked with 403/404
    assert r_stud_anon.status_code in {403, 404, 302, 401}, f"Anonymous downloaded student-restricted doc: {r_stud_anon.status_code}"
    print(f"[9] Anonymous blocked from downloading student-restricted document (status {r_stud_anon.status_code}).")
    
    # 6b. Try downloading as Logged-in Student
    r_stud_logged = session.get(f"{BASE_URL}/documents/{student_doc_id}/download/")
    assert r_stud_logged.status_code == 200, f"Logged-in student failed to download student doc: {r_stud_logged.status_code}"
    print("[10] Logged-in student allowed to download student-restricted document.")

if __name__ == "__main__":
    run_test()
