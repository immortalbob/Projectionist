"""Spreadsheet layout: every sheet, every column, and what each column means.

The column lists here drive both the Excel/CSV writers and the "About" sheet's
column dictionary, so a column only has to be described once.

Column kinds:
    text      plain text
    int       whole number
    float1/2/3  decimal number shown with 1/2/3 decimal places
    date      calendar date
    datetime  date and time (local time of the computer running the export)
    url       web link (written as a clickable hyperlink where Excel allows)
"""

from __future__ import annotations

from dataclasses import dataclass, field

LIST_SEP = "; "   # separator used inside multi-value cells (names can contain commas)


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    kind: str = "text"
    width: float = 14
    desc: str = ""


@dataclass
class Sheet:
    name: str
    columns: list[Column]
    rows: list[dict] = field(default_factory=list)
    freeze_cols: int = 0
    desc: str = ""


def C(key, header, kind="text", width=14, desc=""):
    return Column(key, header, kind, width, desc)


# ---------------------------------------------------------------------------
# Sheet names
# ---------------------------------------------------------------------------
MOVIES = "Movies"
CAST = "Cast"
CREW = "Crew"
FILES = "Files"
STREAMS = "Streams"
CHAPTERS = "Chapters"
CREDITS_SCENES = "Credits Scenes"
RATINGS = "Ratings"
REVIEWS = "Reviews"
COLLECTIONS = "Collections"
COLLECTION_LIST = "Collection List"
EXTRAS = "Extras"
WATCH_STATUS = "Watch Status"
WATCH_HISTORY = "Watch History"
ABOUT = "About"

# Optional detail sheets, in workbook order, with a one-line description for the GUI.
DETAIL_SHEETS = [
    (CAST, "Every actor and the character they play, in billing order"),
    (CREW, "Directors, writers and producers with their exact credit"),
    (FILES, "One row per video file (versions and multi-part files)"),
    (STREAMS, "Every video, audio and subtitle track in every file"),
    (CHAPTERS, "Chapter names and start/end times"),
    (CREDITS_SCENES, "Scenes during or after the end credits - when to stay"),
    (RATINGS, "Every rating Plex stores (IMDb, Rotten Tomatoes, TMDb...)"),
    (REVIEWS, "Critic review excerpts with publication and verdict"),
    (COLLECTIONS, "Which movies are in which collections - regular and smart"),
    (COLLECTION_LIST, "Every collection, with smart collections' filters"),
    (EXTRAS, "Trailers, featurettes, deleted scenes and other extras"),
    (WATCH_STATUS, "Per-user play count, last played, resume point and rating"),
    (WATCH_HISTORY, "Every play Plex logged: when, who and on which device"),
]
DETAIL_SHEET_NAMES = [name for name, _ in DETAIL_SHEETS]

# Leading columns repeated on detail sheets so each one is readable on its own.
_ID = C("plex_id", "Plex ID", "int", 9, "Movie's Plex ID - matches the Plex ID column on the Movies sheet.")
_TITLE = C("title", "Title", "text", 32, "Movie title.")
_YEAR = C("year", "Year", "int", 6, "Movie release year.")
_LIB = C("library", "Library", "text", 16, "Plex library the movie is in.")
_MOVIE_KEYS = [_ID, _TITLE, _YEAR, _LIB]


MOVIE_COLUMNS = [
    # --- Identity -----------------------------------------------------------
    C("plex_id", "Plex ID", "int", 9,
      "Plex's internal ID for the movie (the 'ratingKey', as in /library/metadata/<id>). "
      "Every other sheet links back to the movie through this number."),
    C("library", "Library", "text", 16, "Plex library (section) the movie belongs to."),
    C("title", "Title", "text", 36, "Movie title as shown in Plex."),
    C("title_sort", "Sort Title", "text", 28, "Title Plex sorts by (leading 'The', 'A' etc. removed)."),
    C("original_title", "Original Title", "text", 24, "Title in the original language, when different."),
    C("edition", "Edition", "text", 16, "Edition name, e.g. Director's Cut, Extended, Theatrical."),
    C("year", "Year", "int", 6, "Release year."),
    C("release_date", "Release Date", "date", 11, "Original release date."),
    C("content_rating", "Content Rating", "text", 9, "Certification, e.g. PG-13, R, TV-MA."),
    C("content_rating_age", "Common Sense Age", "int", 8,
      "Common Sense Media's recommended minimum age for this particular movie (not a fixed age for the "
      "certification - it varies from film to film)."),
    C("content_advisory", "Common Sense Summary", "text", 40,
      "Common Sense Media's one-line parental review, e.g. 'Intense sci-fi thriller has violence, strong language.'"),
    C("common_sense_rating", "Common Sense Rating (1-5)", "int", 9,
      "Common Sense Media's quality rating for the movie, 1 to 5 stars."),
    C("runtime_min", "Runtime (min)", "int", 8, "Running time in minutes (metadata, or the file's length if metadata has none)."),
    C("tagline", "Tagline", "text", 30, "Marketing tagline."),
    C("summary", "Summary", "text", 60, "Plot summary."),
    # --- Classification -----------------------------------------------------
    C("genres", "Genres", "text", 26, "Genres, in Plex's order."),
    C("countries", "Countries", "text", 20, "Production countries."),
    C("studio", "Studio", "text", 22, "Primary studio."),
    C("production_companies", "Production Companies", "text", 30, "Every production company credited."),
    C("collections", "Collections", "text", 24,
      "Collections the movie is in - regular ones and smart (saved-filter) ones. A smart collection is included "
      "when its filter could be re-run exactly; the Collection List sheet shows any that couldn't."),
    C("labels", "Labels", "text", 16, "User-assigned labels."),
    # --- People ---------------------------------------------------------------
    C("directors", "Directors", "text", 24,
      "Directors: Plex's 'Director' credits. If there are none, other directing credits such as 'Action "
      "Director' are used, and failing that any director credit Plex filed under another job (it lists James Gunn "
      "as 'Script Supervisor' on Guardians Vol. 2). Assistant directors never appear here - every credit, with "
      "its job, is on the Crew sheet."),
    C("writers", "Writers", "text", 28, "All writing credits - screenplay, story, novel, characters... (details on Crew sheet)."),
    C("producers", "Producers", "text", 28,
      "Producer credits of any kind (producer, executive producer, associate producer...). Other production roles, "
      "such as a producer's assistant or casting, are on the Crew sheet only."),
    C("cast", "Cast", "text", 40, "All credited actors in billing order."),
    C("cast_characters", "Cast & Characters", "text", 50, "'Actor (Character)' in billing order."),
    C("cast_count", "Cast Count", "int", 7, "Number of credited actors."),
    # --- Ratings --------------------------------------------------------------
    C("critic_rating", "Critic Rating", "float1", 8,
      "Plex's headline critic rating, 0-10 (normally the Rotten Tomatoes Tomatometer / 10)."),
    C("critic_rating_source", "Critic Rating Source", "text", 20, "Where the critic rating comes from, and its verdict."),
    C("audience_rating", "Audience Rating", "float1", 8, "Plex's headline audience rating, 0-10."),
    C("audience_rating_source", "Audience Rating Source", "text", 20, "Where the audience rating comes from, and its verdict."),
    C("imdb_rating", "IMDb Rating", "float1", 8, "IMDb user rating, 0-10."),
    C("rt_critic", "RT Tomatometer %", "int", 9, "Rotten Tomatoes critic score, percent."),
    C("rt_critic_verdict", "RT Critic Verdict", "text", 9, "Fresh or Rotten."),
    C("rt_audience", "RT Audience %", "int", 9, "Rotten Tomatoes audience (Popcornmeter) score, percent."),
    C("rt_audience_verdict", "RT Audience Verdict", "text", 9, "Upright (liked) or Spilled (disliked)."),
    C("tmdb_rating", "TMDb Rating", "float1", 8, "The Movie Database user rating, 0-10."),
    C("other_ratings", "Other Ratings", "text", 16, "Ratings from any other source Plex stores."),
    C("critic_reviews", "Critic Reviews", "int", 8, "Number of critic review excerpts stored (see Reviews sheet)."),
    # --- External IDs & links -----------------------------------------------
    C("imdb_id", "IMDb ID", "text", 11, "IMDb title ID (tt...)."),
    C("tmdb_id", "TMDb ID", "text", 9, "The Movie Database ID."),
    C("tvdb_id", "TVDb ID", "text", 9, "TheTVDB ID."),
    C("other_ids", "Other IDs", "text", 14, "Any other external IDs, as source:id."),
    C("imdb_url", "IMDb URL", "url", 16, "Link to the IMDb page."),
    C("tmdb_url", "TMDb URL", "url", 16, "Link to the TMDb page."),
    C("plex_guid", "Plex GUID", "text", 22,
      "Plex's ID for this item. For an edition it ends in '/edition/<name>' taken from this server's file names, "
      "and local:// GUIDs only mean something on this server - use Plex Movie ID to match across servers."),
    C("plex_movie_id", "Plex Movie ID", "text", 22,
      "Plex's server-independent ID for the film itself (plex://movie/...), the same for every edition and on "
      "every Plex server. Blank for movies Plex couldn't match online."),
    C("plex_url", "Plex Discover URL", "url", 16, "Link to the movie on watch.plex.tv."),
    # --- Viewing ----------------------------------------------------------------
    C("owner_plays", "Owner Plays", "int", 7,
      "Plex's play count for the server owner. This is Plex's current watched state: 'Mark as unwatched' resets "
      "it to 0 (the Watch History sheet keeps every play Plex logged)."),
    C("owner_last_played", "Owner Last Played", "datetime", 16,
      "When the server owner last played it, per Plex's current watched state."),
    C("owner_rating", "Owner Rating", "float1", 8,
      "Server owner's own star rating, 0-10 (5 stars = 10). For a film none of whose copies has a rating of its own, "
      "a rating the owner gave an earlier edition of it (see Owner Rating Source)."),
    C("owner_rating_source", "Owner Rating Source", "text", 12,
      "Where the Owner Rating comes from. This copy = the rating Plex shows on this copy. An earlier edition = the "
      "owner rated the film before Plex gave this copy its edition (or renamed it) and Plex kept the rating on the "
      "old edition, so Plex shows this copy as unrated - and smart collections that filter on ratings leave it "
      "out. An earlier edition's rating is only used when no copy of the film has a rating of its own; the app's "
      "tabs count it too. The Watch Status sheet shows Plex's own rating on each copy."),
    C("owner_resume_at", "Owner Resume At", "text", 9, "Where the owner stopped part-way through (h:mm:ss)."),
    C("total_plays", "Total Plays (All Users)", "int", 8,
      "Plex's play counts summed across every user of the server (reset per user by 'Mark as unwatched')."),
    C("watched_by", "Watched By", "text", 24,
      "Users whose Plex watched state currently counts at least one play (see Watch Status sheet)."),
    C("last_played", "Last Played (Any User)", "datetime", 16,
      "Most recent play by anyone, per Plex's current watched state. A play later undone with 'Mark as unwatched' "
      "isn't counted here, but still appears on the Watch History sheet."),
    # --- Library dates --------------------------------------------------------
    C("added_at", "Date Added", "datetime", 16, "When the movie was added to Plex."),
    C("updated_at", "Last Updated", "datetime", 16, "When Plex last changed the movie's record."),
    C("refreshed_at", "Metadata Refreshed", "datetime", 16, "When Plex last refreshed metadata from the internet."),
    C("unavailable_since", "Unavailable Since", "datetime", 16,
      "Set when Plex can no longer find the file (movie is in the trash)."),
    # --- Media (main version) -------------------------------------------------
    C("versions", "Versions", "int", 7, "Number of versions (separate copies) of the movie. Columns below describe the first."),
    C("resolution", "Resolution", "text", 8,
      "Resolution class, fitted to Plex's own resolution filter: 4K, 1080p, 720p, 576p, 480p (height 464 or "
      "more) or SD - so a 720x360 widescreen DVD rip is SD, as in Plex. The 1080p/720p boundary is a best fit for "
      "a few unusual sizes (e.g. 1374x1036). 8K for 7680-wide video."),
    C("width", "Width", "int", 6, "Video width in pixels."),
    C("height", "Height", "int", 6, "Video height in pixels."),
    C("aspect_ratio", "Aspect Ratio", "float2", 7, "Display aspect ratio (e.g. 1.78, 2.40)."),
    C("video_codec", "Video Codec", "text", 12, "Video codec."),
    C("video_profile", "Video Profile", "text", 10, "Codec profile, e.g. main 10, high."),
    C("bit_depth", "Bit Depth", "int", 6, "Video bit depth."),
    C("hdr", "HDR", "text", 20, "Dynamic range: SDR, HDR10, HLG or Dolby Vision (with profile)."),
    C("frame_rate", "Frame Rate", "float3", 8, "Frames per second."),
    C("container", "Container", "text", 8, "File container, e.g. MKV, MP4."),
    C("bitrate_mbps", "Bitrate (Mbps)", "float1", 8, "Overall bitrate in megabits per second."),
    C("audio", "Audio (Main Track)", "text", 24, "Format and channels of the main (default) audio track."),
    C("audio_tracks", "Audio Tracks", "int", 7, "Number of audio tracks."),
    C("audio_languages", "Audio Languages", "text", 22, "Languages of the audio tracks."),
    C("subtitle_tracks", "Subtitle Tracks", "int", 7, "Number of subtitle tracks (embedded and external files)."),
    C("subtitle_languages", "Subtitle Languages", "text", 22, "Languages of the subtitle tracks."),
    C("file_path", "File Path", "text", 50, "Full path of the file as the Plex server sees it (multi-part files joined with ' | ')."),
    C("file_name", "File Name", "text", 36, "File name only."),
    C("folder", "Folder", "text", 36, "Folder containing the file."),
    C("file_size_gb", "File Size (GB)", "float2", 8, "Size of the version's file(s) in gigabytes (10^9 bytes)."),
    C("parts", "Parts", "int", 5, "Number of files the version is split across (e.g. CD1/CD2)."),
    C("total_size_gb", "Total Size (GB)", "float2", 8, "Size of every version combined."),
    # --- Other ----------------------------------------------------------------
    C("chapters", "Chapters", "int", 7, "Number of chapters."),
    C("markers", "Markers", "text", 30, "Detected credits / intro markers with start-end times."),
    C("stay_after_credits", "Stay After Credits?", "text", 10,
      "Anything to stay for once the end credits start? Worked out from Plex's credits markers. Yes = a likely "
      "mid- or post-credits scene, or outtakes (see Credits Scene Times). Maybe = footage between or after the "
      "credits that could just be a title card, exit music or on-screen text. None found = Plex found the end "
      "credits and nothing inside or after them - but a short gag at the very end, or outtakes shown alongside the "
      "credits, can hide inside Plex's last credits marker. Blank = nothing to go on: Plex hasn't marked credits on "
      "this movie, or what it marked was followed by minutes more film (on-screen text taken for credits, or an old "
      "film's exit music)."),
    C("credits_scenes", "Credits Scenes", "int", 7,
      "How many scenes Plex's markers show during or after the end credits, Likely and Maybe (see the Credits "
      "Scenes sheet). Blank when there's nothing to go on."),
    C("credits_scene_times", "Credits Scene Times", "text", 36,
      "Where each scene starts, e.g. '2:05:12 mid-credits (28 s)'. '(maybe)' marks the unsure ones; the Credits "
      "Scenes sheet says why."),
    C("credits_start", "Credits Start", "text", 9,
      "When the end credits begin (h:mm:ss), from Plex's credits markers. When a Maybe comes first, this is where "
      "the credits-like stretch before it starts."),
    C("runtime_before_credits_min", "Runtime Before Credits (min)", "int", 9,
      "Minutes of film before the end credits start."),
    C("extras", "Extras", "text", 30, "Count of each kind of extra (see Extras sheet)."),
    C("poster_url", "Poster URL", "url", 18, "Web address of the selected poster image (blank for custom uploads)."),
    C("art_url", "Background Art URL", "url", 18, "Web address of the selected background art."),
    C("logo_url", "Logo URL", "url", 18, "Web address of the selected title logo."),
    C("square_art_url", "Square Art URL", "url", 18, "Web address of the selected square artwork."),
    C("other_tags", "Other Tags", "text", 16, "Any other kind of tag Plex has attached (moods, styles...)."),
    C("locked_fields", "Locked Field IDs", "text", 10,
      "Plex's internal numbers for fields locked against automatic metadata updates (Plex locks a field when "
      "it is edited by hand or its artwork is chosen). Raw IDs exactly as Plex stores them."),
]

CAST_COLUMNS = _MOVIE_KEYS + [
    C("order", "Billing Order", "int", 8, "Position in the credits, starting at 1."),
    C("actor", "Actor", "text", 26, "Actor's name."),
    C("character", "Character", "text", 30, "Character played."),
    C("person_id", "Plex Person ID", "text", 26, "Plex's ID for the person - the same across every movie they're in."),
    C("photo_url", "Photo URL", "text", 30, "Web address of the person's photo."),
]

CREW_COLUMNS = _MOVIE_KEYS + [
    C("department", "Department", "text", 11, "Directing, Writing or Production."),
    C("order", "Order", "int", 6, "Position within the department's credits, starting at 1."),
    C("name", "Name", "text", 26, "Person's name."),
    C("job", "Credit", "text", 22, "Exact credit, e.g. Director, Screenplay, Novel, Executive Producer."),
    C("person_id", "Plex Person ID", "text", 26, "Plex's ID for the person - the same across every movie they're in."),
    C("photo_url", "Photo URL", "text", 30, "Web address of the person's photo."),
]

FILE_COLUMNS = _MOVIE_KEYS + [
    C("version", "Version", "int", 7, "Version number (1 = the main version)."),
    C("part", "Part", "int", 5, "Part number within the version."),
    C("file_path", "File Path", "text", 60, "Full path as the Plex server sees it."),
    C("file_name", "File Name", "text", 40, "File name only."),
    C("folder", "Folder", "text", 40, "Folder containing the file."),
    C("size_bytes", "Size (bytes)", "int", 14, "Exact file size in bytes."),
    C("size_gb", "Size (GB)", "float2", 8, "File size in gigabytes (10^9 bytes)."),
    C("duration_min", "Duration (min)", "float1", 8, "Length of this file in minutes."),
    C("container", "Container", "text", 8, "File container."),
    C("resolution", "Resolution", "text", 8, "Resolution class, as on the Movies sheet."),
    C("width", "Width", "int", 6, "Video width in pixels."),
    C("height", "Height", "int", 6, "Video height in pixels."),
    C("aspect_ratio", "Aspect Ratio", "float2", 7, "Display aspect ratio."),
    C("video_codec", "Video Codec", "text", 12, "Video codec."),
    C("video_profile", "Video Profile", "text", 10, "Codec profile."),
    C("bit_depth", "Bit Depth", "int", 6, "Video bit depth."),
    C("hdr", "HDR", "text", 20, "Dynamic range."),
    C("frame_rate", "Frame Rate", "float3", 8, "Frames per second."),
    C("bitrate_mbps", "Bitrate (Mbps)", "float1", 8, "Overall bitrate in megabits per second."),
    C("audio", "Audio (Main Track)", "text", 24, "Format and channels of the main audio track."),
    C("audio_tracks", "Audio Tracks", "int", 7, "Number of audio tracks in this file."),
    C("audio_languages", "Audio Languages", "text", 22, "Languages of the audio tracks."),
    C("subtitle_tracks", "Subtitle Tracks", "int", 7, "Number of subtitle tracks in this file."),
    C("subtitle_languages", "Subtitle Languages", "text", 22, "Languages of the subtitle tracks."),
    C("file_hash", "Plex File Hash", "text", 20, "Plex's fingerprint of the file."),
    C("opensubtitles_hash", "OpenSubtitles Hash", "text", 16, "OpenSubtitles.org hash, handy for fetching subtitles."),
]

STREAM_COLUMNS = _MOVIE_KEYS + [
    C("version", "Version", "int", 7, "Version number (1 = the main version)."),
    C("part", "Part", "int", 5, "Part number within the version."),
    C("stream_index", "Track #", "int", 6, "Track position inside the file (blank for external subtitle files)."),
    C("stream_type", "Type", "text", 8, "Video, Audio or Subtitle."),
    C("codec", "Codec", "text", 10, "Codec as Plex reports it."),
    C("format", "Format", "text", 22, "Readable format, e.g. 'Dolby TrueHD Atmos 7.1', 'HEVC 4K HDR10'."),
    C("language_code", "Language Code", "text", 8, "Language code as stored by Plex."),
    C("language", "Language", "text", 14, "Language name."),
    C("track_title", "Track Title", "text", 28, "Track title embedded in the file."),
    C("channels", "Channels", "int", 7, "Audio channel count."),
    C("channel_layout", "Channel Layout", "text", 10, "Audio layout, e.g. 5.1, 7.1, stereo."),
    C("bitrate_kbps", "Bitrate (kbps)", "int", 9, "Track bitrate in kilobits per second."),
    C("sampling_rate", "Sample Rate (Hz)", "int", 9, "Audio sample rate."),
    C("bit_depth", "Bit Depth", "int", 6, "Audio or video bit depth."),
    C("resolution", "Resolution", "text", 9, "Video track resolution (width x height)."),
    C("frame_rate", "Frame Rate", "float3", 8, "Video frames per second."),
    C("hdr", "HDR", "text", 20, "Video dynamic range."),
    C("default", "Default", "text", 7, "Yes if the track is flagged as default."),
    C("forced", "Forced", "text", 7, "Yes if the track is flagged as forced."),
    C("external", "External File", "text", 8, "Yes for sidecar subtitle files (.srt etc.)."),
]

CHAPTER_COLUMNS = _MOVIE_KEYS + [
    C("chapter", "Chapter #", "int", 8, "Chapter number."),
    C("name", "Chapter Name", "text", 28, "Chapter title, when the file has one."),
    C("start", "Start", "text", 9, "Start time (h:mm:ss)."),
    C("end", "End", "text", 9, "End time (h:mm:ss)."),
    C("start_sec", "Start (sec)", "float3", 10, "Start time in seconds."),
    C("end_sec", "End (sec)", "float3", 10, "End time in seconds."),
]

CREDITS_SCENE_COLUMNS = _MOVIE_KEYS + [
    C("scene", "Scene #", "int", 7, "1 for the first scene once the credits start, 2 for the next..."),
    C("kind", "Kind", "text", 16,
      "Mid-credits (more credits follow it) or After the credits (it runs to the end of the file)."),
    C("verdict", "Verdict", "text", 8,
      "Likely = the usual shape of a mid/post-credits scene or outtakes. Maybe = see Why Maybe."),
    C("starts_at", "Starts At", "text", 9, "Where the footage starts (h:mm:ss) - skip to here to watch it."),
    C("ends_at", "Ends At", "text", 9, "Where it stops: the next stretch of credits, or the end of the file."),
    C("length_sec", "Length (sec)", "int", 8, "Seconds of footage."),
    C("why_maybe", "Why Maybe", "text", 44, "For a Maybe: why it might not be a real scene."),
    C("credits_before", "Credits Before It", "text", 18, "The stretch of credits just before it (h:mm:ss-h:mm:ss)."),
    C("starts_at_sec", "Starts At (sec)", "float1", 9, "Start in seconds, for scripts and players."),
]

RATING_COLUMNS = _MOVIE_KEYS + [
    C("source", "Source", "text", 16, "Rating source."),
    C("rating_type", "Type", "text", 9, "Critic or Audience."),
    C("value", "Rating (0-10)", "float1", 9, "Rating on Plex's 0-10 scale."),
    C("display", "Display", "text", 9, "Rating as the source shows it, e.g. 87%, 8.7/10."),
    C("verdict", "Verdict", "text", 10, "Fresh / Rotten / Upright / Spilled for Rotten Tomatoes."),
]

REVIEW_COLUMNS = _MOVIE_KEYS + [
    C("order", "Order", "int", 6, "Order Plex lists the review in."),
    C("critic", "Critic", "text", 22, "Reviewer's name."),
    C("publication", "Publication", "text", 22, "Publication the review appeared in."),
    C("verdict", "Verdict", "text", 8, "Fresh or Rotten."),
    C("review", "Review", "text", 80, "Review excerpt."),
    C("link", "Link", "text", 30, "Link to the full review."),
]

COLLECTION_COLUMNS = [
    C("collection", "Collection", "text", 28, "Collection name."),
    C("collection_type", "Type", "text", 8,
      "Regular (movies added by hand) or Smart (Plex fills it from a saved filter - see Collection List)."),
    C("collection_summary", "Collection Summary", "text", 40, "The collection's own description."),
] + _MOVIE_KEYS

COLLECTION_LIST_COLUMNS = [
    C("collection", "Collection", "text", 28, "Collection name."),
    C("library", "Library", "text", 16, "Plex library the collection belongs to."),
    C("collection_type", "Type", "text", 8, "Regular or Smart."),
    C("plex_count", "Plex Count", "int", 8, "How many movies Plex says the collection holds (as of the dump)."),
    C("exported", "Movies Exported", "int", 9,
      "How many movies are listed for it on the Collections sheet and in the Movies sheet's Collections column."),
    C("status", "Status", "text", 50,
      "For a smart collection: whether its filter could be re-run exactly (the result must match Plex's own count), "
      "and if not, why. Smart collections based on watched state or ratings use the server owner's."),
    C("filter", "Smart Filter", "text", 50, "A smart collection's saved filter, in words."),
    C("collection_summary", "Collection Summary", "text", 40, "The collection's own description."),
    C("collection_id", "Collection Plex ID", "int", 10, "Plex's ID for the collection itself."),
]

EXTRA_COLUMNS = _MOVIE_KEYS + [
    C("extra_type", "Extra Type", "text", 16, "Trailer, Featurette, Deleted Scene, Behind the Scenes..."),
    C("extra_title", "Extra Title", "text", 40, "Title of the extra."),
    C("order", "Order", "int", 6, "Order Plex lists the extra in."),
    C("main_trailer", "Main Trailer", "text", 7, "Yes for the extra Plex plays as the movie's main trailer."),
    C("duration_min", "Duration (min)", "float1", 8, "Length in minutes."),
    C("released", "Released", "date", 11, "When the extra was released."),
    C("explicit", "Explicit", "text", 7, "Yes for red-band / explicit trailers."),
    C("source", "Source", "text", 12, "Local file or Online (streamed by Plex)."),
    C("location", "File / Address", "text", 50, "File path for local extras, provider address for online ones."),
    C("thumbnail_url", "Thumbnail URL", "text", 30, "Web address of the extra's thumbnail image."),
    C("extra_id", "Extra Plex ID", "int", 9, "Plex ID of the extra itself."),
]

WATCH_STATUS_COLUMNS = _MOVIE_KEYS + [
    C("user", "User", "text", 18, "Plex user."),
    C("plays", "Plays", "int", 6,
      "Plex's play count for this user - its current watched state: 'Mark as unwatched' resets it to 0 and "
      "'Mark as played' counts as a play (Watch History lists every play Plex logged)."),
    C("last_played", "Last Played", "datetime", 16, "When this user last played it, per Plex's current watched state."),
    C("resume_at", "Resume At", "text", 9, "Where this user stopped part-way through (h:mm:ss)."),
    C("rating", "User Rating", "float1", 8,
      "This user's star rating, 0-10 (5 stars = 10), as Plex has it on this copy (a rating left on an earlier "
      "edition isn't here: see the Movies sheet's Owner Rating Source)."),
]

WATCH_HISTORY_COLUMNS = [
    C("viewed_at", "Viewed At", "datetime", 16,
      "When Plex logged the play. The log also includes titles marked as played by hand (often several a few "
      "seconds apart) and plays later undone with 'Mark as unwatched'."),
    C("user", "User", "text", 18, "Plex user."),
    C("device", "Device", "text", 18, "Player device/app, where Plex recorded one."),
] + _MOVIE_KEYS

SHEET_COLUMNS = {
    MOVIES: MOVIE_COLUMNS,
    CAST: CAST_COLUMNS,
    CREW: CREW_COLUMNS,
    FILES: FILE_COLUMNS,
    STREAMS: STREAM_COLUMNS,
    CHAPTERS: CHAPTER_COLUMNS,
    CREDITS_SCENES: CREDITS_SCENE_COLUMNS,
    RATINGS: RATING_COLUMNS,
    REVIEWS: REVIEW_COLUMNS,
    COLLECTIONS: COLLECTION_COLUMNS,
    COLLECTION_LIST: COLLECTION_LIST_COLUMNS,
    EXTRAS: EXTRA_COLUMNS,
    WATCH_STATUS: WATCH_STATUS_COLUMNS,
    WATCH_HISTORY: WATCH_HISTORY_COLUMNS,
}

# How many leading columns stay visible while scrolling sideways.
FREEZE_COLS = {MOVIES: 3, COLLECTIONS: 1, COLLECTION_LIST: 1, WATCH_HISTORY: 1}

SHEET_DESCRIPTIONS = {MOVIES: "One row per movie - the master table."}
SHEET_DESCRIPTIONS.update(dict(DETAIL_SHEETS))
