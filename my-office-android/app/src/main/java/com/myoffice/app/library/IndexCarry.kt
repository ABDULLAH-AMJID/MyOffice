package com.myoffice.app.library

/**
 * The rule that keeps a rescan from throwing away the search index.
 *
 * `replaceAll` rewrites every row on every scan (SQLite's CONFLICT_REPLACE deletes and inserts,
 * so unlisted columns fall back to their defaults). Before this helper existed the rescan
 * silently reset `text_state` to pending for all files, which is why a library of a few
 * thousand documents restarted indexing from the same place forever.
 *
 * Kept free of Android classes so the rule itself can be unit tested on the JVM.
 */
object IndexCarry {

    /** The part of an existing row that must survive a rescan. */
    data class Previous(
        val modified: Long,
        val textState: Int,
        val textModified: Long,
        val failures: Int,
        val lastUsed: Long,
    )

    /** What the new row should say about the index. */
    data class State(
        val textState: Int,
        val textModified: Long,
        val failures: Int,
        val lastUsed: Long,
    )

    fun stateFor(previous: Previous?, modified: Long, lastUsed: Long): State {
        if (previous == null) {
            // A file we have never seen starts at the beginning of the queue.
            return State(LibraryIndex.TEXT_PENDING, 0, 0, lastUsed)
        }
        if (previous.modified != modified) {
            // The file was edited, so its old text is stale — read it again, and give it a
            // fresh pair of attempts.
            return State(LibraryIndex.TEXT_PENDING, 0, 0, previous.lastUsed)
        }
        return State(previous.textState, previous.textModified, previous.failures, previous.lastUsed)
    }
}
