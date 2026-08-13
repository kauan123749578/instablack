"""
Endpoint → Instagram internal screen/fragment mappings.

Maps API endpoints to the values Instagram's Android app sends in:
  - x-ig-client-endpoint  (which screen made the request)
  - x-fb-friendly-name    (human-readable request label)
  - x-ig-nav-chain        (navigation breadcrumbs)

Sources: reverse-engineered from captured Instagram 433.x traffic.
"""

# x-fb-friendly-name patterns
# Format: "IgApi: {endpoint_path}"
# Some endpoints use custom names; most follow this pattern.

# x-ig-client-endpoint values
# Format: "{Fragment}:{screen}:{position}"
# These identify which Android Fragment/activity triggered the request.

ENDPOINT_META: dict[str, dict] = {
    # ── Feed / Timeline ──────────────────────────────────────────────
    "feed/timeline": {
        "client_endpoint": "MainFeedFragment:feed_timeline:1",
        "friendly_name": "IgApi: feed/timeline/",
        "nav_section": "feed_timeline",
    },
    "feed/best": {
        "client_endpoint": "MainFeedFragment:feed_best:1",
        "friendly_name": "IgApi: feed/best/",
        "nav_section": "feed_best",
    },
    "feed/old_best": {
        "client_endpoint": "MainFeedFragment:feed_old_best:1",
        "friendly_name": "IgApi: feed/old_best/",
        "nav_section": "feed_old_best",
    },
    "feed/reels_tray": {
        "client_endpoint": "ClipsTabFragment:reels_tray:1",
        "friendly_name": "IgApi: feed/reels_tray/",
        "nav_section": "reels_tray",
    },
    "feed/explore": {
        "client_endpoint": "ExploreFragment:explore:1",
        "friendly_name": "IgApi: feed/explore/",
        "nav_section": "explore",
    },
    # ── Clips / Reels ────────────────────────────────────────────────
    "clips/connected/": {
        "client_endpoint": "ClipsViewerFragment:clips_connected:1",
        "friendly_name": "IgApi: clips/connected/",
        "nav_section": "clips_connected",
    },
    "clips/discover/": {
        "client_endpoint": "ClipsViewerFragment:clips_discover:1",
        "friendly_name": "IgApi: clips/discover/",
        "nav_section": "clips_discover",
    },
    "clips/discover/stream/": {
        "client_endpoint": "ClipsViewerFragment:clips_discover_stream:1",
        "friendly_name": "IgApi: clips/discover/stream/",
        "nav_section": "clips_discover_stream",
    },
    "clips/discover/social/": {
        "client_endpoint": "ClipsViewerFragment:clips_discover_social:1",
        "friendly_name": "IgApi: clips/discover/social/",
        "nav_section": "clips_discover_social",
    },
    "clips/play/": {
        "client_endpoint": "ClipsViewerFragment:clips_play:1",
        "friendly_name": "IgApi: clips/play/",
        "nav_section": "clips_play",
    },
    "clips/write_seen_state/": {
        "client_endpoint": "ClipsViewerFragment:clips_write_seen_state:1",
        "friendly_name": "IgApi: clips/write_seen_state/",
        "nav_section": "clips_write_seen",
    },
    # ── Media ─────────────────────────────────────────────────────────
    "media/{media_id}/info/": {
        "client_endpoint": "MediaFragment:media_info:1",
        "friendly_name": "IgApi: media/info/",
        "nav_section": "media_info",
    },
    "media/{media_id}/comment/": {
        "client_endpoint": "CommentListBottomsheetFragment:comments_v2:2",
        "friendly_name": "IgApi: media/{media_id}/comment/",
        "nav_section": "comments_v2",
    },
    "media/{media_id}/comments/": {
        "client_endpoint": "CommentListBottomsheetFragment:comments_v2:2",
        "friendly_name": "IgApi: media/{media_id}/comments/",
        "nav_section": "comments_v2",
    },
    "media/{media_id}/like/": {
        "client_endpoint": "MediaFragment:media_like:1",
        "friendly_name": "IgApi: media/{media_id}/like/",
        "nav_section": "media_like",
    },
    "media/{media_id}/unlike/": {
        "client_endpoint": "MediaFragment:media_unlike:1",
        "friendly_name": "IgApi: media/{media_id}/unlike/",
        "nav_section": "media_unlike",
    },
    "media/{media_id}/save/": {
        "client_endpoint": "MediaFragment:media_save:1",
        "friendly_name": "IgApi: media/{media_id}/save/",
        "nav_section": "media_save",
    },
    "media/{media_id}/unsave/": {
        "client_endpoint": "MediaFragment:media_unsave:1",
        "friendly_name": "IgApi: media/{media_id}/unsave/",
        "nav_section": "media_unsave",
    },
    "media/{media_id}/edit_media/": {
        "client_endpoint": "MediaFragment:media_edit:1",
        "friendly_name": "IgApi: media/{media_id}/edit_media/",
        "nav_section": "media_edit",
    },
    "media/{media_id}/delete/": {
        "client_endpoint": "MediaFragment:media_delete:1",
        "friendly_name": "IgApi: media/{media_id}/delete/",
        "nav_section": "media_delete",
    },
    "media/{media_id}/archive/": {
        "client_endpoint": "MediaFragment:media_archive:1",
        "friendly_name": "IgApi: media/{media_id}/archive/",
        "nav_section": "media_archive",
    },
    "media/{media_id}/unarchive/": {
        "client_endpoint": "MediaFragment:media_unarchive:1",
        "friendly_name": "IgApi: media/{media_id}/unarchive/",
        "nav_section": "media_unarchive",
    },
    "media/{media_id}/comment/like/": {
        "client_endpoint": "CommentListBottomsheetFragment:comment_like:1",
        "friendly_name": "IgApi: media/{media_id}/comment/like/",
        "nav_section": "comment_like",
    },
    "media/{media_id}/comment/unlike/": {
        "client_endpoint": "CommentListBottomsheetFragment:comment_unlike:1",
        "friendly_name": "IgApi: media/{media_id}/comment/unlike/",
        "nav_section": "comment_unlike",
    },
    "media/{media_id}/comments/disable/": {
        "client_endpoint": "MediaFragment:media_comments_disable:1",
        "friendly_name": "IgApi: media/{media_id}/comments/disable/",
        "nav_section": "comments_disable",
    },
    "media/{media_id}/comments/enable/": {
        "client_endpoint": "MediaFragment:media_comments_enable:1",
        "friendly_name": "IgApi: media/{media_id}/comments/enable/",
        "nav_section": "comments_enable",
    },
    # ── Media V2 ──────────────────────────────────────────────────────
    "media/media_info/": {
        "client_endpoint": "MediaFragment:media_info_v2:1",
        "friendly_name": "IgApi: media/media_info/",
        "nav_section": "media_info_v2",
    },
    # ── User ──────────────────────────────────────────────────────────
    "users/{user_id}/full_detail/": {
        "client_endpoint": "ProfileFragment:profile_full_detail:1",
        "friendly_name": "IgApi: users/full_detail/",
        "nav_section": "profile_full_detail",
    },
    "users/{user_id}/usernameinfo/": {
        "client_endpoint": "ProfileFragment:profile_username_info:1",
        "friendly_name": "IgApi: users/usernameinfo/",
        "nav_section": "profile_username_info",
    },
    "users/{user_id}/follower_and_following_status/": {
        "client_endpoint": "ProfileFragment:profile_follow_status:1",
        "friendly_name": "IgApi: users/follower_and_following_status/",
        "nav_section": "profile_follow_status",
    },
    "users/{user_id}/follow/": {
        "client_endpoint": "ProfileFragment:profile_follow:1",
        "friendly_name": "IgApi: users/{user_id}/follow/",
        "nav_section": "profile_follow",
    },
    "users/{user_id}/unfollow/": {
        "client_endpoint": "ProfileFragment:profile_unfollow:1",
        "friendly_name": "IgApi: users/{user_id}/unfollow/",
        "nav_section": "profile_unfollow",
    },
    "users/{user_id}/remove_follower/": {
        "client_endpoint": "ProfileFragment:profile_remove_follower:1",
        "friendly_name": "IgApi: users/{user_id}/remove_follower/",
        "nav_section": "profile_remove_follower",
    },
    "users/{user_id}/block/": {
        "client_endpoint": "ProfileFragment:profile_block:1",
        "friendly_name": "IgApi: users/{user_id}/block/",
        "nav_section": "profile_block",
    },
    "users/{user_id}/unblock/": {
        "client_endpoint": "ProfileFragment:profile_unblock:1",
        "friendly_name": "IgApi: users/{user_id}/unblock/",
        "nav_section": "profile_unblock",
    },
    "users/{user_id}/set_private/": {
        "client_endpoint": "ProfileFragment:profile_set_private:1",
        "friendly_name": "IgApi: users/{user_id}/set_private/",
        "nav_section": "profile_set_private",
    },
    "users/{user_id}/set_public/": {
        "client_endpoint": "ProfileFragment:profile_set_public:1",
        "friendly_name": "IgApi: users/{user_id}/set_public/",
        "nav_section": "profile_set_public",
    },
    "users/{user_id}/account_details/": {
        "client_endpoint": "AccountFragment:account_details:1",
        "friendly_name": "IgApi: users/account_details/",
        "nav_section": "account_details",
    },
    "users/{user_id}/account_finance_detail/": {
        "client_endpoint": "AccountFragment:account_finance_detail:1",
        "friendly_name": "IgApi: users/account_finance_detail/",
        "nav_section": "account_finance_detail",
    },
    "friendships/{user_id}/followers/": {
        "client_endpoint": "FollowListFragment:followers_list:1",
        "friendly_name": "IgApi: friendships/followers/",
        "nav_section": "followers_list",
    },
    "friendships/{user_id}/following/": {
        "client_endpoint": "FollowListFragment:following_list:1",
        "friendly_name": "IgApi: friendships/following/",
        "nav_section": "following_list",
    },
    "friendships/{user_id}/followers_rankmed/": {
        "client_endpoint": "FollowListFragment:followers_ranked:1",
        "friendly_name": "IgApi: friendships/followers_rankmed/",
        "nav_section": "followers_ranked",
    },
    "friendships/{user_id}/following_rankmed/": {
        "client_endpoint": "FollowListFragment:following_ranked:1",
        "friendly_name": "IgApi: friendships/following_rankmed/",
        "nav_section": "following_ranked",
    },
    # ── Story ─────────────────────────────────────────────────────────
    "feed/user/{user_id}/story/": {
        "client_endpoint": "StoryViewerFragment:story_viewer:1",
        "friendly_name": "IgApi: feed/user/story/",
        "nav_section": "story_viewer",
    },
    "media/{media_id}/story_views/": {
        "client_endpoint": "StoryViewerFragment:story_views:1",
        "friendly_name": "IgApi: media/story_views/",
        "nav_section": "story_views",
    },
    # ── Direct ────────────────────────────────────────────────────────
    "direct_v2/inbox/": {
        "client_endpoint": "InboxFragment:direct_inbox:1",
        "friendly_name": "IgApi: direct_v2/inbox/",
        "nav_section": "direct_inbox",
    },
    "direct_v2/threads/": {
        "client_endpoint": "DirectThreadFragment:direct_thread:1",
        "friendly_name": "IgApi: direct_v2/threads/",
        "nav_section": "direct_thread",
    },
    "direct_v2/threads/{thread_id}/": {
        "client_endpoint": "DirectThreadFragment:direct_thread_detail:1",
        "friendly_name": "IgApi: direct_v2/threads/detail/",
        "nav_section": "direct_thread_detail",
    },
    "direct_v2/threads/{thread_id}/send_item/": {
        "client_endpoint": "DirectThreadFragment:direct_send_item:1",
        "friendly_name": "IgApi: direct_v2/threads/send_item/",
        "nav_section": "direct_send_item",
    },
    "direct_v2/threads/{thread_id}/update_title/": {
        "client_endpoint": "DirectThreadFragment:direct_update_title:1",
        "friendly_name": "IgApi: direct_v2/threads/update_title/",
        "nav_section": "direct_update_title",
    },
    "direct_v2/threads/{thread_id}/add_users/": {
        "client_endpoint": "DirectThreadFragment:direct_add_users:1",
        "friendly_name": "IgApi: direct_v2/threads/add_users/",
        "nav_section": "direct_add_users",
    },
    "direct_v2/threads/{thread_id}/leave/": {
        "client_endpoint": "DirectThreadFragment:direct_leave:1",
        "friendly_name": "IgApi: direct_v2/threads/leave/",
        "nav_section": "direct_leave",
    },
    "direct_v2/threads/{thread_id}/hide/": {
        "client_endpoint": "DirectThreadFragment:direct_hide:1",
        "friendly_name": "IgApi: direct_v2/threads/hide/",
        "nav_section": "direct_hide",
    },
    "direct_v2/threads/{thread_id}/mute/": {
        "client_endpoint": "DirectThreadFragment:direct_mute:1",
        "friendly_name": "IgApi: direct_v2/threads/mute/",
        "nav_section": "direct_mute",
    },
    "direct_v2/threads/{thread_id}/unmute/": {
        "client_endpoint": "DirectThreadFragment:direct_unmute:1",
        "friendly_name": "IgApi: direct_v2/threads/unmute/",
        "nav_section": "direct_unmute",
    },
    "direct_v2/threads/{thread_id}/mark_seen/": {
        "client_endpoint": "DirectThreadFragment:direct_mark_seen:1",
        "friendly_name": "IgApi: direct_v2/threads/mark_seen/",
        "nav_section": "direct_mark_seen",
    },
    "direct_v2/threads/{thread_id}/release/": {
        "client_endpoint": "DirectThreadFragment:direct_release:1",
        "friendly_name": "IgApi: direct_v2/threads/release/",
        "nav_section": "direct_release",
    },
    "direct_v2/hashtag/": {
        "client_endpoint": "DirectThreadFragment:direct_hashtag:1",
        "friendly_name": "IgApi: direct_v2/hashtag/",
        "nav_section": "direct_hashtag",
    },
    "direct_v2/hashtag/threads/": {
        "client_endpoint": "DirectThreadFragment:direct_hashtag_threads:1",
        "friendly_name": "IgApi: direct_v2/hashtag/threads/",
        "nav_section": "direct_hashtag_threads",
    },
    # ── Comment ───────────────────────────────────────────────────────
    "media/{media_id}/comment/": {
        "client_endpoint": "CommentListBottomsheetFragment:comments_v2:2",
        "friendly_name": "IgApi: media/{media_id}/comment/",
        "nav_section": "comments_v2",
    },
    "media/{media_id}/comment/bulk_delete/": {
        "client_endpoint": "CommentListBottomsheetFragment:comments_bulk_delete:1",
        "friendly_name": "IgApi: media/comment/bulk_delete/",
        "nav_section": "comments_bulk_delete",
    },
    # ── Hashtag ───────────────────────────────────────────────────────
    "tags/{hashtag}/info/": {
        "client_endpoint": "HashtagFragment:hashtag_info:1",
        "friendly_name": "IgApi: tags/info/",
        "nav_section": "hashtag_info",
    },
    "tags/{hashtag}/sections/": {
        "client_endpoint": "HashtagFragment:hashtag_sections:1",
        "friendly_name": "IgApi: tags/sections/",
        "nav_section": "hashtag_sections",
    },
    "tags/{hashtag}/recent/": {
        "client_endpoint": "HashtagFragment:hashtag_recent:1",
        "friendly_name": "IgApi: tags/recent/",
        "nav_section": "hashtag_recent",
    },
    "tags/{hashtag}/related/": {
        "client_endpoint": "HashtagFragment:hashtag_related:1",
        "friendly_name": "IgApi: tags/related/",
        "nav_section": "hashtag_related",
    },
    # ── Location ──────────────────────────────────────────────────────
    "locations/{location_id}/info/": {
        "client_endpoint": "LocationFragment:location_info:1",
        "friendly_name": "IgApi: locations/info/",
        "nav_section": "location_info",
    },
    "locations/{location_id}/sections/": {
        "client_endpoint": "LocationFragment:location_sections:1",
        "friendly_name": "IgApi: locations/sections/",
        "nav_section": "location_sections",
    },
    "locations/{location_id}/nearby/": {
        "client_endpoint": "LocationFragment:location_nearby:1",
        "friendly_name": "IgApi: locations/nearby/",
        "nav_section": "location_nearby",
    },
    # ── Collections ───────────────────────────────────────────────────
    "collections/list/": {
        "client_endpoint": "CollectionsFragment:collections_list:1",
        "friendly_name": "IgApi: collections/list/",
        "nav_section": "collections_list",
    },
    "collections/{collection_id}/items/": {
        "client_endpoint": "CollectionsFragment:collections_items:1",
        "friendly_name": "IgApi: collections/items/",
        "nav_section": "collections_items",
    },
    # ── Highlights ────────────────────────────────────────────────────
    "highlights/{user_id}/highlights_tray/": {
        "client_endpoint": "HighlightsFragment:highlights_tray:1",
        "friendly_name": "IgApi: highlights/highlights_tray/",
        "nav_section": "highlights_tray",
    },
    "highlights/{highlight_id}/items/": {
        "client_endpoint": "HighlightsFragment:highlight_items:1",
        "friendly_name": "IgApi: highlights/items/",
        "nav_section": "highlight_items",
    },
    # ── Discover / Explore ────────────────────────────────────────────
    "discover/explore/": {
        "client_endpoint": "ExploreFragment:explore_grid:1",
        "friendly_name": "IgApi: discover/explore/",
        "nav_section": "explore_grid",
    },
    "discover/web_profile_info/": {
        "client_endpoint": "ExploreFragment:web_profile_info:1",
        "friendly_name": "IgApi: discover/web_profile_info/",
        "nav_section": "web_profile_info",
    },
    # ── Search ────────────────────────────────────────────────────────
    "fbsearch/topsearch/": {
        "client_endpoint": "SearchFragment:search_top:1",
        "friendly_name": "IgApi: fbsearch/topsearch/",
        "nav_section": "search_top",
    },
    "fbsearch/igprofile/": {
        "client_endpoint": "SearchFragment:search_profile:1",
        "friendly_name": "IgApi: fbsearch/igprofile/",
        "nav_section": "search_profile",
    },
    # ── IGTV ──────────────────────────────────────────────────────────
    "igtv/{user_id}/get_profile/": {
        "client_endpoint": "IGTVFragment:igtv_profile:1",
        "friendly_name": "IgApi: igtv/get_profile/",
        "nav_section": "igtv_profile",
    },
    # ── Account ───────────────────────────────────────────────────────
    "accounts/login/": {
        "client_endpoint": "LoginFragment:login:1",
        "friendly_name": "IgApi: accounts/login/",
        "nav_section": "login",
    },
    "accounts/logout/": {
        "client_endpoint": "ProfileFragment:logout:1",
        "friendly_name": "IgApi: accounts/logout/",
        "nav_section": "logout",
    },
    "accounts/edit/": {
        "client_endpoint": "EditProfileFragment:edit_profile:1",
        "friendly_name": "IgApi: accounts/edit/",
        "nav_section": "edit_profile",
    },
    "accounts/set_private/": {
        "client_endpoint": "AccountFragment:account_set_private:1",
        "friendly_name": "IgApi: accounts/set_private/",
        "nav_section": "account_set_private",
    },
    "accounts/set_public/": {
        "client_endpoint": "AccountFragment:account_set_public:1",
        "friendly_name": "IgApi: accounts/set_public/",
        "nav_section": "account_set_public",
    },
    "accounts/change_password/": {
        "client_endpoint": "AccountFragment:account_change_password:1",
        "friendly_name": "IgApi: accounts/change_password/",
        "nav_section": "account_change_password",
    },
    "accounts/remove_profile_picture/": {
        "client_endpoint": "EditProfileFragment:remove_profile_picture:1",
        "friendly_name": "IgApi: accounts/remove_profile_picture/",
        "nav_section": "remove_profile_picture",
    },
    "accounts/set_username/": {
        "client_endpoint": "EditProfileFragment:set_username:1",
        "friendly_name": "IgApi: accounts/set_username/",
        "nav_section": "set_username",
    },
    "accounts/set_biography/": {
        "client_endpoint": "EditProfileFragment:set_biography:1",
        "friendly_name": "IgApi: accounts/set_biography/",
        "nav_section": "set_biography",
    },
    "accounts/switch_to_private/": {
        "client_endpoint": "AccountFragment:account_switch_private:1",
        "friendly_name": "IgApi: accounts/switch_to_private/",
        "nav_section": "account_switch_private",
    },
    "accounts/switch_to_business/": {
        "client_endpoint": "AccountFragment:account_switch_business:1",
        "friendly_name": "IgApi: accounts/switch_to_business/",
        "nav_section": "account_switch_business",
    },
    "accounts/convert_to_personal/": {
        "client_endpoint": "AccountFragment:account_convert_personal:1",
        "friendly_name": "IgApi: accounts/convert_to_personal/",
        "nav_section": "account_convert_personal",
    },
    "accounts/update_profile_image/": {
        "client_endpoint": "EditProfileFragment:update_profile_image:1",
        "friendly_name": "IgApi: accounts/update_profile_image/",
        "nav_section": "update_profile_image",
    },
    # ── Notifications ─────────────────────────────────────────────────
    "notifications/": {
        "client_endpoint": "ActivityFragment:activity_feed:1",
        "friendly_name": "IgApi: notifications/",
        "nav_section": "activity_feed",
    },
    "notifications/seen/": {
        "client_endpoint": "ActivityFragment:notifications_seen:1",
        "friendly_name": "IgApi: notifications/seen/",
        "nav_section": "notifications_seen",
    },
    # ── Insights ──────────────────────────────────────────────────────
    "insights/{entity_type}/{entity_id}/": {
        "client_endpoint": "InsightsFragment:insights_detail:1",
        "friendly_name": "IgApi: insights/detail/",
        "nav_section": "insights_detail",
    },
    # ── Creator ───────────────────────────────────────────────────────
    "creator/{action}/": {
        "client_endpoint": "CreatorFragment:creator_action:1",
        "friendly_name": "IgApi: creator/action/",
        "nav_section": "creator_action",
    },
    # ── Shopping ──────────────────────────────────────────────────────
    "shopping/browse/": {
        "client_endpoint": "ShoppingFragment:shopping_browse:1",
        "friendly_name": "IgApi: shopping/browse/",
        "nav_section": "shopping_browse",
    },
    # ── Share ─────────────────────────────────────────────────────────
    "media/{media_id}/share/": {
        "client_endpoint": "ShareFragment:media_share:1",
        "friendly_name": "IgApi: media/{media_id}/share/",
        "nav_section": "media_share",
    },
    # ── Like ──────────────────────────────────────────────────────────
    "media/like/": {
        "client_endpoint": "MediaFragment:media_like:1",
        "friendly_name": "IgApi: media/like/",
        "nav_section": "media_like",
    },
    "media/unlike/": {
        "client_endpoint": "MediaFragment:media_unlike:1",
        "friendly_name": "IgApi: media/unlike/",
        "nav_section": "media_unlike",
    },
    "media/bulk_delete/": {
        "client_endpoint": "MediaFragment:media_bulk_delete:1",
        "friendly_name": "IgApi: media/bulk_delete/",
        "nav_section": "media_bulk_delete",
    },
    # ── Upload ────────────────────────────────────────────────────────
    "media/configure/": {
        "client_endpoint": "MediaFragment:media_configure:1",
        "friendly_name": "IgApi: media/configure/",
        "nav_section": "media_configure",
    },
    "media/configure_to_timeline/": {
        "client_endpoint": "MediaFragment:media_configure_timeline:1",
        "friendly_name": "IgApi: media/configure_to_timeline/",
        "nav_section": "media_configure_timeline",
    },
    "media/configure_to_story/": {
        "client_endpoint": "MediaFragment:media_configure_story:1",
        "friendly_name": "IgApi: media/configure_to_story/",
        "nav_section": "media_configure_story",
    },
    "media/configure_to_clips/": {
        "client_endpoint": "MediaFragment:media_configure_clips:1",
        "friendly_name": "IgApi: media/configure_to_clips/",
        "nav_section": "media_configure_clips",
    },
    # ── QR Code ───────────────────────────────────────────────────────
    "qr_code/": {
        "client_endpoint": "QRCodeFragment:qr_code:1",
        "friendly_name": "IgApi: qr_code/",
        "nav_section": "qr_code",
    },
    "qr_code_url/": {
        "client_endpoint": "QRCodeFragment:qr_code_url:1",
        "friendly_name": "IgApi: qr_code_url/",
        "nav_section": "qr_code_url",
    },
    # ── Notes ─────────────────────────────────────────────────────────
    "notes/": {
        "client_endpoint": "NotesFragment:notes_list:1",
        "friendly_name": "IgApi: notes/",
        "nav_section": "notes_list",
    },
    "notes/create/": {
        "client_endpoint": "NotesFragment:notes_create:1",
        "friendly_name": "IgApi: notes/create/",
        "nav_section": "notes_create",
    },
    # ──xfb / GraphQL ──────────────────────────────────────────────────
    "api/v1/graphql/query/": {
        "client_endpoint": "GraphQLFragment:graphql_query:1",
        "friendly_name": "IgApi: graphql/query/",
        "nav_section": "graphql_query",
    },
    # ── Bloks ─────────────────────────────────────────────────────────
    "bloks/apps/": {
        "client_endpoint": "BloksFragment:bloks_action:1",
        "friendly_name": "IgApi: bloks/apps/",
        "nav_section": "bloks_action",
    },
}


def get_endpoint_meta(endpoint: str) -> dict:
    """
    Look up metadata for an endpoint.

    Tries exact match first, then strips path params (e.g. media IDs, user IDs)
    and retries with the generic pattern.

    Returns a dict with keys: client_endpoint, friendly_name, nav_section.
    Falls back to sensible defaults if not found.
    """
    import re

    # Normalize: strip leading /v1/ or /api/v1/ prefix
    clean = endpoint.lstrip("/")
    for prefix in ("api/v1/", "v1/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break

    # Normalize trailing slash: always keep one
    had_trailing_slash = clean.endswith("/")
    clean = clean.rstrip("/")

    # Exact match
    if clean in ENDPOINT_META:
        return ENDPOINT_META[clean]

    # Build a normalized pattern by replacing all dynamic segments
    # (numeric IDs, UUIDs) with a generic placeholder.
    # Also replace any named placeholders like {media_id}, {user_id} with {id}.
    parts = clean.split("/")
    normalized_parts = []
    for part in parts:
        # Is it a numeric ID?
        if re.match(r"^\d+$", part):
            normalized_parts.append("{id}")
        # Is it a UUID?
        elif re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", part):
            normalized_parts.append("{id}")
        # Is it already a placeholder like {media_id}?
        elif part.startswith("{") and part.endswith("}"):
            normalized_parts.append("{id}")
        else:
            normalized_parts.append(part)

    # Always append trailing slash to match ENDPOINT_META keys
    normalized = "/".join(normalized_parts) + "/"

    # Try exact normalized match
    if normalized in ENDPOINT_META:
        return ENDPOINT_META[normalized]

    # Build a reverse lookup: match endpoint structure against pattern keys.
    # Strategy: compare the structural "shape" (number of segments, which
    # segments are fixed strings vs dynamic IDs).
    for key, meta in ENDPOINT_META.items():
        key_clean = key.strip("/")
        key_parts = key_clean.split("/")

        if len(key_parts) != len(normalized_parts):
            continue

        match = True
        for key_part, norm_part in zip(key_parts, normalized_parts):
            # Key has a named placeholder like {hashtag}, {user_id} — matches any dynamic segment
            if key_part.startswith("{"):
                continue
            # Both are fixed strings — must match exactly
            if key_part == norm_part:
                continue
            # Anything else is a mismatch
            match = False
            break

        if match:
            return meta

    # Default fallback
    return {
        "client_endpoint": "UnknownFragment:unknown:0",
        "friendly_name": f"IgApi: {clean}/",
        "nav_section": "unknown",
    }
