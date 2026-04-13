package partner.authorization

# User-to-department mappings (fallback for local/mock mode).
# In production with OIDC/Keycloak, departments come from JWT group claims.
user_departments_fallback := {
	"carlos@example.com": ["engineering", "software"],
	"luis@example.com": ["engineering", "network"],
	"sharon@example.com": ["engineering", "software", "network", "admin"],
	"josh@example.com": [],
}
