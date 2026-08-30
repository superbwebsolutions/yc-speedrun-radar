"""Source adapters — one module per monitored source.

Each adapter converts a raw upstream response into the shared models
(Company or SocialPost) so the rest of the system never knows where
data came from. Adding a new platform later = adding one file here.
"""
