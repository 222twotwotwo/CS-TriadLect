package lab_dream.controller;

import lab_dream.entity.MemoryFragment;
import lab_dream.exception.GuardException;
import lab_dream.mapper.JourneyMapper;
import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 终章 —— 世界修复之后。
 *
 * 通关不是结束：这里会把玩家这一路的足迹整理成"回忆"，
 * 再递上一张可以截图炫耀的证书。招生入口也藏在终章里。
 */
@RestController
public class FinaleController {

    private final GameStateService gameState;
    private final JourneyMapper journeys;
    private final StoryService story;

    public FinaleController(GameStateService gameState, JourneyMapper journeys,
                            StoryService story) {
        this.gameState = gameState;
        this.journeys = journeys;
        this.story = story;
    }

    /** 全部七片都找齐了，终章才开门 */
    private void requireFinished() {
        if (gameState.nextFragmentId() != null) {
            throw new GuardException("终章之门纹丝不动。门缝里飘出 星野凛 的声音：「还有碎片没找完呢。」");
        }
    }

    /** GET /finale —— 庆典与招募词 */
    @GetMapping("/finale")
    public Map<String, Object> finale() {
        requireFinished();
        String whisper = gameState.state().lastWhisper();
        return Map.of(
                "restored", story.text("/finale/restored"),
                "grantLine", story.text("/finale/grantLine"),
                "echoLine", story.text("/finale/echoLine"),
                "doorLine", story.text("/finale/doorLine"),
                "recruitLine", story.text("/finale/recruitLine"),
                "secondRun", story.text("/finale/secondRun"),
                "certLine", story.text("/finale/certLine"),
                "yourWhisper", whisper == null ? "" : whisper);
    }

    /** GET /finale/certificate —— 通关证书的数据（前端负责画成像素奖状） */
    @GetMapping("/finale/certificate")
    public Map<String, Object> certificate() {
        requireFinished();

        MemoryFragment last = gameState.fragment(7);
        String fixDate = last.getFoundAt() == null ? "" : last.getFoundAt().toString();

        List<String> lines = new ArrayList<>();
        lines.add(story.text("/certificate/lines/steps", "steps", journeys.count()));
        lines.add(story.text("/certificate/lines/fixDate", "date", fixDate));
        String whisper = gameState.state().lastWhisper();
        if (whisper != null && !whisper.isBlank()) {
            lines.add(story.text("/certificate/lines/whisper", "whisper", whisper));
        }
        lines.add(story.text("/certificate/lines/sign"));

        return Map.of(
                "title", story.text("/certificate/title"),
                "subtitle", story.text("/certificate/subtitle"),
                "lines", lines,
                "footer", story.text("/certificate/footer"));
    }
}
