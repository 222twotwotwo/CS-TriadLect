package lab_dream.dto;

/** 样本库里的一行足迹 */
public record JourneyView(long id, String time, String method,
                          String path, String query, int status) {}
