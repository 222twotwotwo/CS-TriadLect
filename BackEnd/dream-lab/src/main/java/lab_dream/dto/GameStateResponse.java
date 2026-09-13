package lab_dream.dto;

import java.util.List;

/** 世界仪表盘：前端每隔几秒来问一次"现在怎么样了" */
public record GameStateResponse(
        int foundCount, int totalCount, Integer nextFragmentId, String nextFragmentTitle,
        List<FragmentView> foundFragments, long totalRequests,
        boolean returning, String lastWhisper) {}
