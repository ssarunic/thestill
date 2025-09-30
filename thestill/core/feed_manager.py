import feedparser
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from ..models.podcast import Podcast, Episode
from .youtube_downloader import YouTubeDownloader


class PodcastFeedManager:
    def __init__(self, storage_path: str = "./data"):
        self.storage_path = Path(storage_path)
        self.feeds_file = self.storage_path / "feeds.json"
        self.storage_path.mkdir(exist_ok=True)
        self.podcasts: List[Podcast] = self._load_podcasts()
        self.youtube_downloader = YouTubeDownloader(str(self.storage_path / "audio"))

    def add_podcast(self, url: str) -> bool:
        """Add a new podcast feed - handles RSS URLs, Apple Podcast URLs, and YouTube URLs"""
        try:
            # Check if this is a YouTube URL
            if self.youtube_downloader.is_youtube_url(url):
                return self._add_youtube_podcast(url)

            # Check if this is an Apple Podcast URL and extract RSS if needed
            rss_url = self._extract_rss_from_apple_url(url)
            if not rss_url:
                rss_url = url  # Assume it's already an RSS URL

            parsed_feed = feedparser.parse(rss_url)
            if parsed_feed.bozo:
                raise ValueError(f"Invalid RSS feed: {rss_url}")

            feed = parsed_feed.feed
            podcast = Podcast(
                title=feed.get('title', 'Unknown Podcast'),
                description=feed.get('description', ''),
                rss_url=rss_url
            )

            if not self._podcast_exists(rss_url):
                self.podcasts.append(podcast)
                self._save_podcasts()
                return True
            return False

        except Exception as e:
            print(f"Error adding podcast {url}: {e}")
            return False

    def remove_podcast(self, rss_url: str) -> bool:
        """Remove a podcast feed"""
        initial_count = len(self.podcasts)
        self.podcasts = [p for p in self.podcasts if str(p.rss_url) != rss_url]
        if len(self.podcasts) < initial_count:
            self._save_podcasts()
            return True
        return False

    def get_new_episodes(self) -> List[tuple[Podcast, List[Episode]]]:
        """Check all feeds for new episodes"""
        new_episodes = []

        for podcast in self.podcasts:
            try:
                # Check if this is a YouTube podcast
                if self.youtube_downloader.is_youtube_url(str(podcast.rss_url)):
                    episodes = self._get_youtube_episodes(podcast)
                    if episodes:
                        new_episodes.append((podcast, episodes))
                    continue

                # Handle regular RSS feeds
                parsed_feed = feedparser.parse(str(podcast.rss_url))
                episodes = []

                for entry in parsed_feed.entries:
                    episode_date = self._parse_date(entry.get('published_parsed'))
                    episode_guid = entry.get('guid', entry.get('id', str(episode_date)))

                    # Check if this episode is already processed
                    already_processed = any(ep.guid == episode_guid and ep.processed for ep in podcast.episodes)
                    if already_processed:
                        continue

                    # Include episode if:
                    # 1. It's newer than last_processed, OR
                    # 2. We have very few processed episodes (indicates tracking was broken)
                    num_processed_episodes = len([ep for ep in podcast.episodes if ep.processed])

                    should_include = (podcast.last_processed is None or
                                    episode_date > podcast.last_processed or
                                    num_processed_episodes < 3)  # Assume most feeds have >3 episodes

                    if should_include:
                        audio_url = self._extract_audio_url(entry)
                        if audio_url:
                            episode = Episode(
                                title=entry.get('title', 'Unknown Episode'),
                                description=entry.get('description', ''),
                                pub_date=episode_date,
                                audio_url=audio_url,
                                duration=entry.get('itunes_duration'),
                                guid=episode_guid
                            )

                            # Check if episode already exists in podcast.episodes (but not processed)
                            existing_episode = next((ep for ep in podcast.episodes if ep.guid == episode_guid), None)
                            if not existing_episode:
                                podcast.episodes.append(episode)

                            episodes.append(episode)

                if episodes:
                    new_episodes.append((podcast, episodes))

            except Exception as e:
                print(f"Error checking feed {podcast.rss_url}: {e}")
                continue

        return new_episodes

    def mark_episode_processed(self, podcast_rss_url: str, episode_guid: str,
                              transcript_path: str = None, summary_path: str = None):
        """Mark an episode as processed"""
        for podcast in self.podcasts:
            if str(podcast.rss_url) == podcast_rss_url:
                # Find existing episode or get episode info from RSS
                episode_found = False
                for episode in podcast.episodes:
                    if episode.guid == episode_guid:
                        episode.processed = True
                        episode.transcript_path = transcript_path
                        episode.summary_path = summary_path
                        episode_found = True
                        break

                # If episode not found in stored episodes, fetch it from RSS and add it
                if not episode_found:
                    try:
                        parsed_feed = feedparser.parse(str(podcast.rss_url))
                        for entry in parsed_feed.entries:
                            entry_guid = entry.get('guid', entry.get('id', ''))
                            if entry_guid == episode_guid:
                                episode_date = self._parse_date(entry.get('published_parsed'))
                                audio_url = self._extract_audio_url(entry)
                                if audio_url:
                                    episode = Episode(
                                        title=entry.get('title', 'Unknown Episode'),
                                        description=entry.get('description', ''),
                                        pub_date=episode_date,
                                        audio_url=audio_url,
                                        duration=entry.get('itunes_duration'),
                                        guid=entry_guid,
                                        processed=True,
                                        transcript_path=transcript_path,
                                        summary_path=summary_path
                                    )
                                    podcast.episodes.append(episode)
                                    break
                    except Exception as e:
                        print(f"Error fetching episode info for {episode_guid}: {e}")

                podcast.last_processed = datetime.now()
                break
        self._save_podcasts()

    def list_podcasts(self) -> List[Podcast]:
        """Return list of all podcasts"""
        return self.podcasts

    def _extract_rss_from_apple_url(self, url: str) -> Optional[str]:
        """Extract RSS feed URL from Apple Podcast URL using iTunes Lookup API"""
        try:
            # Check if this is an Apple Podcast URL
            if 'podcasts.apple.com' not in url and 'itunes.apple.com' not in url:
                return None

            # Extract podcast ID from URL
            # URLs can be like:
            # https://podcasts.apple.com/gb/channel/the-rest-is-politics/id6443145599
            # https://itunes.apple.com/us/podcast/podcast-name/id1234567890
            id_match = re.search(r'id(\d+)', url)
            if not id_match:
                print(f"Could not extract podcast ID from Apple URL: {url}")
                return None

            podcast_id = id_match.group(1)

            # Use iTunes Lookup API to get RSS feed
            lookup_url = f"https://itunes.apple.com/lookup?id={podcast_id}"

            with urllib.request.urlopen(lookup_url) as response:
                data = json.load(response)

            if data.get('resultCount', 0) > 0:
                result = data['results'][0]
                feed_url = result.get('feedUrl')
                if feed_url:
                    print(f"Extracted RSS feed from Apple Podcast: {feed_url}")
                    return feed_url
                else:
                    print(f"No RSS feed URL found for podcast ID {podcast_id}")
                    return None
            else:
                # If the ID doesn't work, try to get the page and extract the real ID
                print(f"No podcast found for ID {podcast_id}, attempting to resolve redirect...")
                return self._resolve_apple_podcast_redirect(url)

        except Exception as e:
            print(f"Error extracting RSS from Apple URL {url}: {e}")
            return None

    def _get_youtube_episodes(self, podcast: Podcast) -> List[Episode]:
        """Get new episodes from a YouTube playlist/channel"""
        try:
            # Get all episodes from YouTube
            all_episodes = self.youtube_downloader.get_episodes_from_playlist(str(podcast.rss_url))

            # Filter out already processed episodes
            new_episodes = []
            for episode in all_episodes:
                already_processed = any(ep.guid == episode.guid and ep.processed for ep in podcast.episodes)
                if not already_processed:
                    # Check if episode already exists in podcast.episodes (but not processed)
                    existing_episode = next((ep for ep in podcast.episodes if ep.guid == episode.guid), None)
                    if not existing_episode:
                        podcast.episodes.append(episode)
                    new_episodes.append(episode)

            return new_episodes

        except Exception as e:
            print(f"Error getting YouTube episodes for {podcast.rss_url}: {e}")
            return []

    def _add_youtube_podcast(self, url: str) -> bool:
        """Add a YouTube playlist/channel as a podcast"""
        try:
            playlist_info = self.youtube_downloader.extract_playlist_info(url)
            if not playlist_info:
                print(f"Could not extract YouTube playlist info from: {url}")
                return False

            # Create podcast entry with YouTube URL
            podcast = Podcast(
                title=playlist_info.get('title', 'Unknown YouTube Podcast'),
                description=playlist_info.get('description', ''),
                rss_url=url  # Store the YouTube URL as the "RSS" URL
            )

            if not self._podcast_exists(url):
                self.podcasts.append(podcast)
                self._save_podcasts()
                print(f"Added YouTube podcast: {podcast.title}")
                return True
            return False

        except Exception as e:
            print(f"Error adding YouTube podcast {url}: {e}")
            return False

    def _resolve_apple_podcast_redirect(self, url: str) -> Optional[str]:
        """Resolve Apple Podcast redirects to get the actual podcast ID"""
        try:
            # Some Apple Podcast URLs redirect to different IDs
            # We'll make a request and follow redirects to get the real URL
            request = urllib.request.Request(url)
            request.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')

            with urllib.request.urlopen(request) as response:
                final_url = response.geturl()
                page_content = response.read().decode('utf-8', errors='ignore')

                # Extract all potential IDs from the page content
                id_matches = re.findall(r'id(\d+)', page_content)

                # Try each ID found on the page
                for potential_id in set(id_matches):  # Use set to avoid duplicates
                    print(f"Trying podcast ID: {potential_id}")

                    lookup_url = f"https://itunes.apple.com/lookup?id={potential_id}"
                    try:
                        with urllib.request.urlopen(lookup_url) as api_response:
                            data = json.load(api_response)

                        if data.get('resultCount', 0) > 0:
                            result = data['results'][0]
                            feed_url = result.get('feedUrl')
                            if feed_url:
                                print(f"Successfully found RSS feed with ID {potential_id}: {feed_url}")
                                return feed_url
                    except Exception as id_error:
                        print(f"Failed to lookup ID {potential_id}: {id_error}")
                        continue

                return None

        except Exception as e:
            print(f"Error resolving Apple Podcast redirect {url}: {e}")
            return None

    def _podcast_exists(self, rss_url: str) -> bool:
        """Check if podcast already exists"""
        return any(str(p.rss_url) == rss_url for p in self.podcasts)

    def _parse_date(self, date_tuple) -> datetime:
        """Parse feedparser date tuple to datetime"""
        if date_tuple:
            try:
                return datetime(*date_tuple[:6])
            except:
                pass
        return datetime.now()

    def _extract_audio_url(self, entry) -> Optional[str]:
        """Extract audio URL from feed entry"""
        for link in entry.get('links', []):
            if link.get('type', '').startswith('audio/'):
                return link.get('href')

        for enclosure in entry.get('enclosures', []):
            if enclosure.get('type', '').startswith('audio/'):
                return enclosure.get('href')

        return None

    def _load_podcasts(self) -> List[Podcast]:
        """Load podcasts from storage"""
        if self.feeds_file.exists():
            try:
                with open(self.feeds_file, 'r') as f:
                    data = json.load(f)
                    return [Podcast(**podcast_data) for podcast_data in data]
            except Exception as e:
                print(f"Error loading podcasts: {e}")
        return []

    def _save_podcasts(self):
        """Save podcasts to storage"""
        try:
            with open(self.feeds_file, 'w') as f:
                json.dump([p.model_dump(mode='json') for p in self.podcasts], f, indent=2)
        except Exception as e:
            print(f"Error saving podcasts: {e}")