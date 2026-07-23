"""病例创建和编辑接口测试。"""

from app.entity.db_models import PatientProfile, User


def test_edit_medical_record_with_existing_visit_date(client, db_session):
    admin_username = "record_edit_admin"
    patient_username = "record_edit_patient"

    admin_register = client.post(
        "/api/auth/register",
        json={
            "username": admin_username,
            "email": "record-edit-admin@example.com",
            "password": "123456",
        },
    )
    assert admin_register.status_code == 201
    admin = db_session.query(User).filter(User.username == admin_username).one()
    admin.user_type = "admin"
    db_session.commit()

    patient_register = client.post(
        "/api/auth/register",
        json={
            "username": patient_username,
            "email": "record-edit-patient@example.com",
            "password": "123456",
        },
    )
    assert patient_register.status_code == 201
    patient = db_session.query(User).filter(User.username == patient_username).one()
    profile = (
        db_session.query(PatientProfile)
        .filter(PatientProfile.user_id == patient.id)
        .one()
    )

    login_response = client.post(
        "/api/auth/login",
        json={"username": admin_username, "password": "123456"},
    )
    assert login_response.status_code == 200
    headers = {
        "Authorization": f"Bearer {login_response.json()['access_token']}"
    }

    create_response = client.post(
        "/api/medical-records",
        headers=headers,
        json={
            "patient_profile_id": profile.id,
            "record_type": "outpatient",
            "chief_complaint": "编辑前主诉",
            "visit_date": "2026-07-22",
        },
    )
    assert create_response.status_code == 201
    record_id = create_response.json()["id"]

    update_response = client.put(
        f"/api/medical-records/{record_id}",
        headers=headers,
        json={
            "record_type": "follow_up",
            "chief_complaint": "编辑后主诉",
            "visit_date": "2026-07-23",
        },
    )
    assert update_response.status_code == 200

    detail_response = client.get(
        f"/api/medical-records/{record_id}", headers=headers
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["record_type"] == "follow_up"
    assert detail["chief_complaint"] == "编辑后主诉"
    assert detail["visit_date"].startswith("2026-07-23")
