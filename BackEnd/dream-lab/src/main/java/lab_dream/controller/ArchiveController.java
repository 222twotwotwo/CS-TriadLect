package lab_dream.controller;

import lab_dream.dto.JourneyView;
import lab_dream.entity.MemoryFragment;
import lab_dream.mapper.JourneyMapper;
import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 档案馆 —— 第 4 关。
 *
 * 这里存放着 journey 表的内容：访客（就是你）发出的每一条请求。
 * 「数据库」第一次露出真容：它不在云里雾里，就是这个项目目录下的 data/world.db 文件。
 * 好奇的话，可以用任何 SQLite 工具打开它——表结构和你在这里看到的一模一样。
 */
@RestController
public class ArchiveController {

    private final GameStateService gameState;
    private final JourneyMapper journeys;
    private final StoryService story;

    public ArchiveController(GameStateService gameState, JourneyMapper journeys,
                             StoryService story) {
        this.gameState = gameState;
        this.journeys = journeys;
        this.story = story;
    }

    /**
     * GET /archive/journey —— 翻开「访客之书」。
     * 第一次翻开，第四片碎片就藏在书页里。
     */
    @GetMapping("/archive/journey")
    public Map<String, Object> book() {
        gameState.requireUnlocked(4);

        List<JourneyView> rows = journeys.findAllByOrderByIdDesc(PageRequest.of(0, 200)).stream()
                .map(j -> new JourneyView(j.getId(), j.getCreatedAt().toString(),
                        j.getMethod(), j.getPath(), j.getQuery(), j.getStatus()))
                .toList();

        boolean firstVisit = !gameState.fragment(4).isFound();
        if (firstVisit) {
            MemoryFragment fragment = gameState.fragment(4);
            return Map.of(
                    "title", story.text("/archive/title"),
                    "intro", story.text("/archive/intro"),
                    "columns", story.node("/archive/columns"),
                    "rows", rows,
                    "bookLine", story.text("/fragments/4/bookLine"),
                    "peep", story.text("/fragments/4/peepLine"),
                    "fragmentCollected", true,
                    "foundLine", gameState.collect(fragment),
                    "nextHint", story.text("/fragments/5/taskLine"));
        }
        return Map.of(
                "title", story.text("/archive/title"),
                "intro", story.text("/archive/intro"),
                "columns", story.node("/archive/columns"),
                "rows", rows,
                "bookLine", "书页静静躺着。你的足迹还在增长——每一条请求都会记在这里。",
                "fragmentCollected", false);
    }
}
