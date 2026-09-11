package partner.authorization

# User departments are sourced from JWT group claims (Keycloak).
# No static fallback - all users must be managed in Keycloak.
# This ensures production-ready user management without hardcoded data.
#
# To add users:
#   - Development: Run scripts/seed-keycloak.sh
#   - Production: Use Keycloak Admin UI or LDAP/SAML integration
#
# JWT token structure:
#   {
#     "email": "user@example.com",
#     "groups": ["engineering", "software", "kubernetes"]
#   }
user_departments_fallback := {
	# Empty - all users managed in Keycloak
}
