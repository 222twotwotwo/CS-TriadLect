package lab_dream.controller;

import lab_dream.dto.VerdictRequest;
import lab_dream.entity.MemoryFragment;
import lab_dream.exception.GuardException;
import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 监控中心 —— 第 5 关。
 *
 * 读服务器日志、从成百上千行输出里揪出异常的那一行，
 * 是后端工程师最日常（也最有成就感）的工作之一。
 * 这一关，你来当一次"值班工程师"。
 */
@RestController
public class MonitorController {

    private final GameStateService gameState;
    private final StoryService story;

    public MonitorController(GameStateService gameState, StoryService story) {
        this.gameState = gameState;
        this.story = story;
    }

    /**
     * GET /monitor/logs —— 调出风暴之夜的日志卷宗。
     * 异常的那一行，就混在里面。仔细看。
     */
    @GetMapping("/monitor/logs")
    public Map<String, Object> logs() {
        gameState.requireUnlocked(5);
        return Map.of(
                "caseTitle", story.text("/storm/caseTitle"),
                "caseIntro", story.text("/storm/caseIntro"),
                "logs", story.node("/storm/logs"));
    }

    /**
     * POST /monitor/verdict —— 锁定真相：提交你认为异常的那条日志的编号。
     * 找对了，碎片 5 和风暴的真相一起浮出水面；找错了，馆长会摇头。
     */
    @PostMapping("/monitor/verdict")
    public Map<String, Object> verdict(@RequestBody VerdictRequest request) {
        gameState.requireUnlocked(5);
        if (request == null || request.logId() == null || request.logId().isBlank()) {
            throw new IllegalArgumentException("提交内容不能为空：需要在请求体里写上 {\"logId\": \"…\"}");
        }

        String anomaly = story.text("/storm/anomalyId");
        if (!anomaly.equals(request.logId().trim())) {
            throw new GuardException("馆长摇了摇头：「编号 " + request.logId().trim()
                    + " 只是一行再普通不过的日志。再看看时间戳之间的缝隙。」");
        }

        MemoryFragment fragment = gameState.fragment(5);
        if (fragment.isFound()) {
            return Map.of("message", "真相已经锁定了，不用提交第二次。", "fragmentCollected", false);
        }
        return Map.of(
                "verdictOk", story.text("/fragments/5/verdictOk"),
                "revealLine", story.text("/fragments/5/revealLine"),
                "fragmentCollected", true,
                "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                "foundLine", gameState.collect(fragment),
                "nextHint", story.text("/fragments/6/taskLine"));
    }
}
