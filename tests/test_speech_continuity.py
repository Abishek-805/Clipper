from clipper import Word, extract_speech_regions, generate_candidates, rank_candidates


def test_speech_proposals_do_not_join_two_lines_across_twenty_seconds_of_silence():
    words = [Word(5, 6, "Hello."), Word(27, 28, "Goodbye.")]
    result = generate_candidates(80, [0.] * 80, [0.] * 80, [], words, 20, 55)
    assert not any("speech" in c["sources"] for c in result["candidates"])


def test_complete_answer_after_long_monologue_survives_ranking():
    words = [Word(i, i + .8, " word" if i != 59 else " end.") for i in range(60)]
    words.extend(Word(i, i + .8, " answer" if i != 82 else " done.") for i in range(61, 83))
    signal = [0.] * 95
    proposals = generate_candidates(95, signal, signal, [], words, 20, 45)["candidates"]
    ranked = rank_candidates(proposals, 95, signal, signal, [], words, 20, 45)
    assert any("speech" in c["sources"] and c["boundary_valid"] and 59.8 <= c["start"] <= 61
               and c["end"] >= 82.8 for c in ranked)


def test_multilingual_sentence_boundaries_survive_closing_quotes():
    words = [Word(0, 1, '「本当？」'), Word(1, 2, 'そうです。'), Word(2, 3, '“Yes!”')]
    assert [u["end"] for u in extract_speech_regions(words) if u["complete_sentence"]] == [1, 2, 3]
